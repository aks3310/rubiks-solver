// ---------------------------------------------------------------------------
// Rubik's cube robot firmware for MKS Gen L v1.0 + 5 x TMC2209 (UART)
//
// Receives text commands from the Raspberry Pi over USB (115200 baud) and
// turns the five cube faces F R B L D. Protocol: see docs/protocol.md.
//
// Built for speed (goal: whole solution in under 2 s):
//  * STEP/DIR pins are written straight to the port registers (no digitalWrite)
//  * the acceleration ramp is a pre-computed delay table (no sqrt per step)
//  * a whole solution arrives in one line, and MOV reports its own run time
//
// Arduino IDE: Board "Arduino Mega or Mega 2560", Processor "ATmega2560".
// Library    : TMCStepper by teemuatlut (Library Manager).
// ---------------------------------------------------------------------------
#include <TMCStepper.h>
#include "config.h"

#define FW_VERSION "1.3"

// ----- drivers ---------------------------------------------------------------
TMC2209Stepper drvF(F_RX, F_TX, R_SENSE, 0);
TMC2209Stepper drvR(R_RX, R_TX, R_SENSE, 0);
TMC2209Stepper drvB(B_RX, B_TX, R_SENSE, 0);
TMC2209Stepper drvL(L_RX, L_TX, R_SENSE, 0);
TMC2209Stepper drvD(&D_SERIAL, R_SENSE, 0);

struct Axis {
  char face;
  uint8_t stepPin, dirPin, enPin;
  bool invert;
  TMC2209Stepper *drv;
  bool softSerial;
  volatile uint8_t *stepPort, *dirPort;   // filled in setup()
  uint8_t stepMask, dirMask;
};

Axis axes[] = {
  {'F', F_STEP, F_DIR, F_EN, F_INVERT, &drvF, true,  0, 0, 0, 0},
  {'R', R_STEP, R_DIR, R_EN, R_INVERT, &drvR, true,  0, 0, 0, 0},
  {'B', B_STEP, B_DIR, B_EN, B_INVERT, &drvB, true,  0, 0, 0, 0},
  {'L', L_STEP, L_DIR, L_EN, L_INVERT, &drvL, true,  0, 0, 0, 0},
  {'D', D_STEP, D_DIR, D_EN, D_INVERT, &drvD, false, 0, 0, 0, 0},
};
const uint8_t N_AXES = sizeof(axes) / sizeof(axes[0]);

float maxSps = DEFAULT_MAX_SPS;
float accel = DEFAULT_ACCEL;
uint16_t settleUs = DEFAULT_SETTLE_US;
uint16_t runCurrent = RUN_CURRENT_MA;
// microsteps past the target before coming back (OVERSHOOT command), 0 = off
long overshootSteps = lround(DEFAULT_OVERSHOOT_DEG * QUARTER_STEPS / 90.0);
bool enabled = false;

// ramp[s] = microseconds to wait after step s of a move (s counted from the
// nearer end). Entries past rampLen use the cruise delay.
uint16_t ramp[RAMP_TABLE_MAX];
uint16_t rampLen = 0;
uint16_t cruiseUs = 0;

void buildRamp(float vmax) {
  const float v0sq = (float)START_SPS * START_SPS;
  cruiseUs = (uint16_t)(1000000.0f / vmax);
  rampLen = 0;
  for (uint16_t s = 0; s < RAMP_TABLE_MAX; s++) {
    float v = sqrt(v0sq + 2.0f * accel * s);
    if (v >= vmax) break;
    ramp[s] = (uint16_t)(1000000.0f / v);
    rampLen++;
  }
  // if the table filled before reaching vmax, cruise at the last table speed
  if (rampLen == RAMP_TABLE_MAX) cruiseUs = ramp[RAMP_TABLE_MAX - 1];
}

// ----- helpers -----------------------------------------------------------------
int8_t axisIndex(char face) {
  for (uint8_t i = 0; i < N_AXES; i++)
    if (axes[i].face == face) return i;
  return -1;
}

char opposite(char f) {
  switch (f) {
    case 'U': return 'D'; case 'D': return 'U';
    case 'R': return 'L'; case 'L': return 'R';
    case 'F': return 'B'; case 'B': return 'F';
  }
  return 0;
}

void setEnabled(bool on) {
  for (uint8_t i = 0; i < N_AXES; i++) digitalWrite(axes[i].enPin, on ? LOW : HIGH);
  enabled = on;
}

void configureDriver(TMC2209Stepper &d) {
  d.begin();
  d.toff(4);
  d.blank_time(24);
  d.I_scale_analog(false);   // current from UART, not the Vref pot
  d.internal_Rsense(false);
  d.mstep_reg_select(true);  // microsteps from UART, not MS1/MS2 pins
  d.pdn_disable(true);       // PDN_UART pin is used for UART
  d.microsteps(MICROSTEPS);
  d.rms_current(runCurrent, HOLD_MULTIPLIER);
  d.en_spreadCycle(USE_SPREADCYCLE);
  d.pwm_autoscale(true);
}

// ----- motion ---------------------------------------------------------------------
// Move up to N_AXES axes together (MOV uses two opposite faces, SHAKE all
// five). steps[] are signed. The longest move follows the ramp table; the
// others are spread over it by Bresenham.
void runMotion(uint8_t n, const int8_t idx[], const long steps[], bool useRamp) {
  long absSteps[N_AXES];
  long master = 0;
  for (uint8_t k = 0; k < n; k++) {
    Axis &a = axes[idx[k]];
    bool forward = (steps[k] >= 0) != a.invert;
    if (forward) *a.dirPort |= a.dirMask; else *a.dirPort &= ~a.dirMask;
    absSteps[k] = labs(steps[k]);
    if (absSteps[k] > master) master = absSteps[k];
  }
  if (master == 0) return;
  delayMicroseconds(5);  // DIR set-up time

  long acc[N_AXES];
  for (uint8_t k = 0; k < n; k++) acc[k] = master / 2;
  const uint16_t jogUs = (uint16_t)(1000000UL / JOG_SPS);
  unsigned long next = micros();
  for (long i = 0; i < master; i++) {
    uint8_t fired = 0;
    for (uint8_t k = 0; k < n; k++) {
      acc[k] += absSteps[k];
      if (acc[k] >= master) {
        acc[k] -= master;
        *axes[idx[k]].stepPort |= axes[idx[k]].stepMask;
        fired |= (1 << k);
      }
    }
    delayMicroseconds(1);   // TMC2209 needs > 100 ns high
    for (uint8_t k = 0; k < n; k++)
      if (fired & (1 << k)) *axes[idx[k]].stepPort &= ~axes[idx[k]].stepMask;

    uint16_t wait;
    if (!useRamp) {
      wait = jogUs;
    } else {
      long s = min(i, master - 1 - i);     // steps from the nearer end
      wait = (s < (long)rampLen) ? ramp[s] : cruiseUs;
    }
    next += wait;
    while ((long)(micros() - next) < 0) { }
  }
}

// One MOV token: turn past the target by overshootSteps in the direction of
// travel, then come back by the same amount. Both faces of an "R+L'" token
// overshoot and return together. JOG never overshoots.
void runTurn(uint8_t n, const int8_t idx[], const long steps[]) {
  if (overshootSteps <= 0) { runMotion(n, idx, steps, true); return; }
  long out[2], back[2];
  for (uint8_t k = 0; k < n; k++) {
    long o = (steps[k] >= 0) ? overshootSteps : -overshootSteps;
    out[k] = steps[k] + o;
    back[k] = -o;
  }
  runMotion(n, idx, out, true);
  if (OVERSHOOT_PAUSE_US) delayMicroseconds(OVERSHOOT_PAUSE_US);
  runMotion(n, idx, back, true);
}

// One part of a token, e.g. R  R'  R2
bool parsePart(const char *p, uint8_t len, int8_t &axis, long &steps, const char *&err) {
  if (len < 1 || len > 2) { err = "BADMOVE"; return false; }
  char face = p[0];
  if (face == 'U') { err = "NOMOTOR U"; return false; }
  axis = axisIndex(face);
  if (axis < 0) { err = "BADMOVE"; return false; }
  if (len == 1) steps = QUARTER_STEPS;
  else if (p[1] == '\'') steps = -QUARTER_STEPS;
  else if (p[1] == '2') steps = 2L * QUARTER_STEPS;
  else { err = "BADMOVE"; return false; }
  return true;
}

// A token: one part, or two opposite faces joined by '+', e.g. R+L'
bool parseToken(const char *tok, uint8_t &n, int8_t idx[], long steps[], const char *&err) {
  const char *plus = strchr(tok, '+');
  n = 0;
  if (!plus) {
    if (!parsePart(tok, strlen(tok), idx[0], steps[0], err)) return false;
    n = 1;
    return true;
  }
  if (!parsePart(tok, plus - tok, idx[0], steps[0], err)) return false;
  if (!parsePart(plus + 1, strlen(plus + 1), idx[1], steps[1], err)) return false;
  if (opposite(axes[idx[0]].face) != axes[idx[1]].face) { err = "NOTOPPOSITE"; return false; }
  n = 2;
  return true;
}

// ----- commands ---------------------------------------------------------------------
#define MAX_TOKENS 64
char line[512];
uint16_t lineLen = 0;

struct Parsed { uint8_t n; int8_t idx[2]; long st[2]; };
Parsed plan[MAX_TOKENS];

void cmdMove(char *args) {
  char *tokens[MAX_TOKENS];
  uint8_t nTok = 0;
  for (char *t = strtok(args, " "); t; t = strtok(NULL, " ")) {
    if (nTok == MAX_TOKENS) { Serial.println(F("ERR TOOMANY max 64 tokens")); return; }
    tokens[nTok++] = t;
  }
  if (nTok == 0) { Serial.println(F("ERR NOMOVES")); return; }
  // parse and check everything before anything moves
  for (uint8_t i = 0; i < nTok; i++) {
    const char *err;
    if (!parseToken(tokens[i], plan[i].n, plan[i].idx, plan[i].st, err)) {
      Serial.print(F("ERR ")); Serial.print(err); Serial.print(' '); Serial.println(tokens[i]);
      return;
    }
  }
  if (!enabled) { setEnabled(true); delay(5); }
  unsigned long t0 = micros();
  for (uint8_t i = 0; i < nTok; i++) {
    runTurn(plan[i].n, plan[i].idx, plan[i].st);
    if (settleUs) delayMicroseconds(settleUs);
  }
  unsigned long ms = (micros() - t0) / 1000UL;
  Serial.print(F("OK ")); Serial.print(nTok); Serial.print(' '); Serial.println(ms);
}

void cmdJog(char *args) {
  char *f = strtok(args, " ");
  char *s = strtok(NULL, " ");
  int8_t a = f ? axisIndex(f[0]) : -1;
  if (a < 0 || !s) { Serial.println(F("ERR USAGE JOG <F|R|B|L|D> <steps>")); return; }
  if (!enabled) { setEnabled(true); delay(5); }
  int8_t idx[1] = {a};
  long st[1] = {atol(s)};
  runMotion(1, idx, st, false);
  Serial.println(F("OK"));
}

// SHAKE <deg> <cycles> [faces]: wiggle the faces back and forth together to
// let a stiff or slightly misaligned cube settle. Each face goes +deg, then
// -deg/+deg `cycles` times, then back to 0, so it always ends exactly where
// it started. Faces default to all five, e.g. SHAKE 3 5 or SHAKE 3 5 FB.
void cmdShake(char *args) {
  char *ds = strtok(args, " ");
  char *cs = ds ? strtok(NULL, " ") : NULL;
  char *fs = cs ? strtok(NULL, " ") : NULL;
  double deg = ds ? atof(ds) : 0;
  long cycles = cs ? atol(cs) : 0;
  if (deg < 0.5 || deg > MAX_SHAKE_DEG || cycles < 1 || cycles > 50) {
    Serial.println(F("ERR USAGE SHAKE <deg 0.5-15> <cycles 1-50> [faces]"));
    return;
  }
  int8_t idx[N_AXES];
  uint8_t n = 0;
  if (!fs) {
    for (uint8_t i = 0; i < N_AXES; i++) idx[n++] = i;
  } else {
    for (char *p = fs; *p; p++) {
      int8_t a = axisIndex(*p);
      if (a < 0) { Serial.print(F("ERR BADFACE ")); Serial.println(*p); return; }
      bool dup = false;
      for (uint8_t k = 0; k < n; k++) if (idx[k] == a) dup = true;
      if (!dup) idx[n++] = a;
    }
  }
  long amp = lround(deg * QUARTER_STEPS / 90.0);
  if (amp < 1) amp = 1;
  long out[N_AXES], swing[N_AXES], swingBack[N_AXES], home[N_AXES];
  for (uint8_t k = 0; k < n; k++) {
    out[k] = amp; swing[k] = -2 * amp; swingBack[k] = 2 * amp; home[k] = -amp;
  }
  if (!enabled) { setEnabled(true); delay(5); }
  unsigned long t0 = micros();
  runMotion(n, idx, out, true);
  for (long c = 0; c < cycles; c++) {
    runMotion(n, idx, swing, true);
    runMotion(n, idx, swingBack, true);
  }
  runMotion(n, idx, home, true);
  if (settleUs) delayMicroseconds(settleUs);
  unsigned long ms = (micros() - t0) / 1000UL;
  Serial.print(F("OK ")); Serial.println(ms);
}

void cmdDrivers() {
  for (uint8_t i = 0; i < N_AXES; i++) {
    TMC2209Stepper &d = *axes[i].drv;
    uint8_t r = d.test_connection();
    Serial.print(F("DRV ")); Serial.print(axes[i].face);
    if (r == 0) {
      Serial.print(F(" ok version=0x")); Serial.print(d.version(), HEX);
      Serial.print(F(" current_mA=")); Serial.print(d.rms_current());
      Serial.print(F(" microsteps=")); Serial.println(d.microsteps());
    } else {
      Serial.print(F(" FAIL code=")); Serial.print(r);
      Serial.println(r == 1 ? F(" (no reply: check UART wire / 1k resistor / power)")
                            : F(" (all zeros: check wiring)"));
    }
  }
  Serial.println(F("OK"));
}

void handleLine(char *s) {
  while (*s == ' ') s++;
  for (char *p = s; *p; p++) *p = toupper(*p);
  char *args = strchr(s, ' ');
  if (args) *args++ = '\0'; else args = s + strlen(s);

  if (!strcmp(s, "PING")) { Serial.println(F("PONG")); Serial.println(F("OK")); }
  else if (!strcmp(s, "VER")) { Serial.println(F("rubik-fw " FW_VERSION)); Serial.println(F("OK")); }
  else if (!strcmp(s, "MOV")) cmdMove(args);
  else if (!strcmp(s, "JOG")) cmdJog(args);
  else if (!strcmp(s, "SHAKE")) cmdShake(args);
  else if (!strcmp(s, "EN")) { setEnabled(atoi(args) != 0); Serial.println(F("OK")); }
  else if (!strcmp(s, "SPEED")) {
    char *vs = strtok(args, " ");
    char *as = vs ? strtok(NULL, " ") : NULL;
    long v = vs ? atol(vs) : 0;
    long a = as ? atol(as) : 0;
    if (v < 100 || v > 20000 || a < 1000 || a > 2000000L) {
      Serial.println(F("ERR USAGE SPEED <max_sps 100-20000> <accel 1000-2000000>"));
      return;
    }
    maxSps = v; accel = a;
    buildRamp(maxSps);
    Serial.print(F("RAMP steps=")); Serial.print(rampLen);
    Serial.print(F(" cruise_us=")); Serial.println(cruiseUs);
    Serial.println(F("OK"));
  }
  else if (!strcmp(s, "SETTLE")) {
    long us = atol(args);
    if (us < 0 || us > 50000) { Serial.println(F("ERR RANGE 0-50000 us")); return; }
    settleUs = us;
    Serial.println(F("OK"));
  }
  else if (!strcmp(s, "OVERSHOOT")) {
    // OVERSHOOT <deg>: 0 = off; decimals allowed, e.g. OVERSHOOT 7.5
    while (*args == ' ') args++;
    char *end;
    double deg = strtod(args, &end);
    if (end == args || deg < 0 || deg > MAX_OVERSHOOT_DEG) {
      Serial.println(F("ERR USAGE OVERSHOOT <deg 0-30>"));
      return;
    }
    overshootSteps = lround(deg * QUARTER_STEPS / 90.0);
    Serial.print(F("OVERSHOOT steps=")); Serial.println(overshootSteps);
    Serial.println(F("OK"));
  }
  else if (!strcmp(s, "CUR")) {
    long ma = atol(args);
    if (ma < 200 || ma > 1600) { Serial.println(F("ERR RANGE 200-1600 mA")); return; }
    runCurrent = ma;
    for (uint8_t i = 0; i < N_AXES; i++) axes[i].drv->rms_current(runCurrent, HOLD_MULTIPLIER);
    Serial.println(F("OK"));
  }
  else if (!strcmp(s, "DRV")) cmdDrivers();
  else if (*s == '\0') { /* empty line */ }
  else { Serial.print(F("ERR UNKNOWN ")); Serial.println(s); }
}

// ----- setup / loop ------------------------------------------------------------------
void setup() {
  Serial.begin(HOST_BAUD);
  for (uint8_t i = 0; i < N_AXES; i++) {
    Axis &a = axes[i];
    pinMode(a.stepPin, OUTPUT);
    pinMode(a.dirPin, OUTPUT);
    pinMode(a.enPin, OUTPUT);
    digitalWrite(a.stepPin, LOW);
    a.stepPort = portOutputRegister(digitalPinToPort(a.stepPin));
    a.stepMask = digitalPinToBitMask(a.stepPin);
    a.dirPort = portOutputRegister(digitalPinToPort(a.dirPin));
    a.dirMask = digitalPinToBitMask(a.dirPin);
  }
  setEnabled(false);
  buildRamp(maxSps);

  D_SERIAL.begin(TMC_HW_BAUD);
  for (uint8_t i = 0; i < N_AXES; i++) {
    if (axes[i].softSerial) axes[i].drv->beginSerial(TMC_SW_BAUD);
    configureDriver(*axes[i].drv);
  }
  Serial.println(F("READY rubik-fw " FW_VERSION));
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      line[lineLen] = '\0';
      handleLine(line);
      lineLen = 0;
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    }
  }
}
