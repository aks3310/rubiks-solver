// ---------------------------------------------------------------------------
// Speed test - MKS Gen L v1.0 + 5 x BTT TMC2209 v1.3, STANDALONE, 1/8 step
// No libraries.
//
// Step pulses come from a hardware timer interrupt (Timer1), so the timing
// is exact even at very high step rates. Each move speeds up with constant
// acceleration, cruises at the top speed, and slows down to stop exactly on
// the requested step count.
//
// On power-up it spins each motor (X, Y, Z, E0, E1) in turn at TOP speed.
// Then it waits for commands in the Serial Monitor (115200 baud, newline):
//
//   g            run the selected motor(s) at the current settings
//   w            sweep: run the selected motor(s) at rising speeds, so you
//                can hear where the motor starts to stall
//   s 20000      set top speed in steps/s   (at 1/8: 1600 steps = 1 rev)
//   a 60000      set acceleration in steps/s^2
//   r 10         set revolutions per run
//   m 0..4       select one motor (0=X 1=Y 2=Z 3=E0 4=E1),  m a = all
//   ?            print settings and help
//
// TAKE THE CUBE OUT (or loosen the adapters) before running: the motors
// spin many full turns.
//
// Jumpers: no MS1 / MS2 / MS3 jumpers under the drivers  ->  1/8 microstep
// (the TMC2209 still interpolates each step to 1/256 internally).
//
// Arduino IDE: Board "Arduino Mega or Mega 2560", Processor "ATmega2560".
// ---------------------------------------------------------------------------

// ----- settings --------------------------------------------------------------
const int   MICROSTEPS      = 8;                      // no MS jumpers
const long  STEPS_PER_REV   = 200L * MICROSTEPS;      // 1600

float topSps   = 16000;    // steps/s  (16000 = 600 rpm). Raise to find your limit.
float accel    = 30000;    // steps/s^2
float revs     = 10;       // revolutions per run
const float START_SPS = 500;      // speed of the first and last steps
const float MAX_SPS   = 60000;    // hard cap (2250 rpm) - far above what a NEMA 17 can do

// ----- MKS Gen L v1.0 pins (same as RAMPS 1.4) -------------------------------
struct Motor {
  const char *name;
  uint8_t stepPin, dirPin, enPin;
};

Motor motors[] = {
  {"X",  54, 55, 38},
  {"Y",  60, 61, 56},
  {"Z",  46, 48, 62},
  {"E0", 26, 28, 24},
  {"E1", 36, 34, 30},
};
const uint8_t N_MOTORS = sizeof(motors) / sizeof(motors[0]);
int8_t selected = -1;      // -1 = all motors

// ----- step generation (Timer1, CTC mode, 2 MHz tick) ------------------------
volatile uint8_t  *stepPort;
volatile uint8_t   stepMask;
volatile int32_t   stepsLeft = 0;
volatile uint16_t  nextOcr   = 4000;

ISR(TIMER1_COMPA_vect) {
  if (stepsLeft > 0) {
    *stepPort |= stepMask;
    __builtin_avr_delay_cycles(10);       // ~0.6 us high (TMC2209 needs > 100 ns)
    *stepPort &= ~stepMask;
    stepsLeft--;
  }
  OCR1A = nextOcr;                        // safe here: TCNT1 has just restarted at 0
}

uint16_t ocrFor(float sps) {
  float ticks = 2000000.0f / sps;         // 16 MHz / 8 prescaler = 2 MHz
  if (ticks > 65535.0f) ticks = 65535.0f;
  if (ticks < 34.0f) ticks = 34.0f;        // ~59k steps/s, leaves time for the ISR
  return (uint16_t)ticks - 1;
}

void timerSetup() {
  TCCR1A = 0;
  TCCR1B = (1 << WGM12) | (1 << CS11);    // CTC, prescaler 8
  TIMSK1 = 0;
}

int32_t readStepsLeft() {
  noInterrupts();
  int32_t v = stepsLeft;
  interrupts();
  return v;
}

// Spin one motor `steps` pulses with a trapezoid speed profile.
void spin(const Motor &m, int32_t steps, bool clockwise) {
  digitalWrite(m.dirPin, clockwise ? HIGH : LOW);
  delayMicroseconds(20);

  stepPort = portOutputRegister(digitalPinToPort(m.stepPin));
  stepMask = digitalPinToBitMask(m.stepPin);

  float top = min(topSps, MAX_SPS);
  float v = START_SPS;
  float peak = v;
  uint16_t o = ocrFor(v);

  noInterrupts();
  nextOcr = o;
  OCR1A = o;
  TCNT1 = 0;
  stepsLeft = steps;
  TIFR1 = (1 << OCF1A);                  // clear any old match
  TIMSK1 |= (1 << OCIE1A);               // start stepping
  interrupts();

  unsigned long t0 = micros();
  unsigned long last = t0;
  while (true) {
    int32_t left = readStepsLeft();
    if (left <= 0) break;
    unsigned long now = micros();
    if (now - last >= 500) {             // update the speed every 0.5 ms
      float dt = (now - last) * 1e-6f;
      last = now;
      float stopSteps = (v * v - START_SPS * START_SPS) / (2.0f * accel);
      if (left <= stopSteps + 2) v -= accel * dt;   // time to slow down
      else if (v < top)          v += accel * dt;   // still speeding up
      if (v > top) v = top;
      if (v < START_SPS) v = START_SPS;
      if (v > peak) peak = v;
      o = ocrFor(v);
      noInterrupts();
      nextOcr = o;
      interrupts();
    }
  }
  TIMSK1 &= ~(1 << OCIE1A);              // stop stepping

  float secs = (micros() - t0) * 1e-6f;
  Serial.print(F("  ")); Serial.print(m.name);
  Serial.print(F(": ")); Serial.print(steps); Serial.print(F(" steps in "));
  Serial.print(secs, 3); Serial.print(F(" s, peak "));
  Serial.print((long)peak); Serial.print(F(" steps/s = "));
  Serial.print((long)(peak * 60.0f / STEPS_PER_REV)); Serial.println(F(" rpm"));
}

// ----- runs -----------------------------------------------------------------------
void runSelected() {
  int32_t steps = (int32_t)(revs * STEPS_PER_REV);
  Serial.print(F("Run: top ")); Serial.print((long)topSps);
  Serial.print(F(" steps/s (")); Serial.print((long)(topSps * 60.0f / STEPS_PER_REV));
  Serial.print(F(" rpm), accel ")); Serial.print((long)accel);
  Serial.print(F(" steps/s^2, ")); Serial.print(revs); Serial.println(F(" rev"));
  float needed = topSps * topSps / accel;          // steps to speed up AND slow down
  if (steps < needed)
    Serial.println(F("  note: too few revs to reach top speed - raise r or a"));
  for (uint8_t i = 0; i < N_MOTORS; i++) {
    if (selected >= 0 && selected != i) continue;
    spin(motors[i], steps, true);
    delay(300);
  }
}

void sweep() {
  const float levels[] = {4000, 8000, 12000, 16000, 20000, 24000, 28000, 32000, 40000};
  float savedTop = topSps, savedRevs = revs;
  Serial.println(F("Sweep: listen for the speed where a motor stops turning and just whines."));
  for (uint8_t k = 0; k < sizeof(levels) / sizeof(levels[0]); k++) {
    topSps = levels[k];
    // enough turns to reach the speed, hold it for 2 turns, and stop
    revs = topSps * topSps / accel / STEPS_PER_REV + 2;
    runSelected();
    delay(700);
  }
  topSps = savedTop;
  revs = savedRevs;
  Serial.println(F("Sweep done. Set your top speed ~20% below the stall speed with: s <steps/s>"));
}

void printHelp() {
  Serial.println(F("Commands: g=run  w=sweep  s <sps>  a <accel>  r <revs>  m <0-4|a>  ?=help"));
  Serial.print(F("Now: top ")); Serial.print((long)topSps);
  Serial.print(F(" steps/s, accel ")); Serial.print((long)accel);
  Serial.print(F(", revs ")); Serial.print(revs);
  Serial.print(F(", motor ")); Serial.println(selected < 0 ? "all" : motors[selected].name);
}

// ----- serial commands ------------------------------------------------------------
char line[32];
uint8_t len = 0;

void handle(char *s) {
  while (*s == ' ') s++;
  char c = *s;
  char *arg = s + 1;
  while (*arg == ' ') arg++;
  if (c == 'g') runSelected();
  else if (c == 'w') sweep();
  else if (c == 's') { float v = atof(arg); if (v >= START_SPS && v <= MAX_SPS) topSps = v; printHelp(); }
  else if (c == 'a') { float v = atof(arg); if (v >= 1000 && v <= 2000000) accel = v; printHelp(); }
  else if (c == 'r') { float v = atof(arg); if (v > 0 && v <= 100) revs = v; printHelp(); }
  else if (c == 'm') { selected = (*arg == 'a') ? -1 : constrain(atoi(arg), 0, N_MOTORS - 1); printHelp(); }
  else if (c == '?') printHelp();
}

void setup() {
  Serial.begin(115200);
  for (uint8_t i = 0; i < N_MOTORS; i++) {
    pinMode(motors[i].stepPin, OUTPUT);
    pinMode(motors[i].dirPin, OUTPUT);
    pinMode(motors[i].enPin, OUTPUT);
    digitalWrite(motors[i].stepPin, LOW);
    digitalWrite(motors[i].enPin, LOW);   // EN is active LOW: drivers on
  }
  timerSetup();
  delay(1000);
  Serial.println(F("Speed test: MKS Gen L + TMC2209 standalone, 1/8 step, no libraries"));
  printHelp();
  runSelected();
  printHelp();
}

void loop() {
  while (Serial.available()) {
    char ch = Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') { line[len] = '\0'; handle(line); len = 0; }
    else if (len < sizeof(line) - 1) line[len++] = ch;
  }
}
