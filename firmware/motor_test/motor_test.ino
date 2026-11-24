// ---------------------------------------------------------------------------
// Motor test - MKS Gen L v1.0 + 5 x BTT TMC2209 v1.3, STANDALONE, 1/8 step
// No libraries.
//
// Type a motor and a number of steps in the Serial Monitor (115200 baud,
// line ending "Newline") and that one motor turns by that many steps:
//
//   X 400       X motor, 400 steps                 (400 steps = 90 degrees)
//   Y -400      Y motor, 400 steps the other way
//   E1 800      E1 motor, 800 steps                (800 steps = 180 degrees)
//   E0 1600     E0 motor, one full turn            (1600 steps = 360 degrees)
//
// Motors are named by their MKS Gen L driver socket: X, Y, Z, E0, E1.
//
// Other commands:
//   speed 6000      top speed in steps/s
//   accel 150000    acceleration in steps/s^2
//   off             disable all motors (they can be turned by hand)
//   on              enable all motors
//   ?               help and current settings
//
// Steps are STEP pulses at 1/8 microstepping (no MS1/MS2/MS3 jumpers):
// 200 full steps x 8 = 1600 per revolution, 400 per quarter turn.
//
// Arduino IDE: Board "Arduino Mega or Mega 2560", Processor "ATmega2560".
// ---------------------------------------------------------------------------

const int  MICROSTEPS    = 8;
const long STEPS_PER_REV = 200L * MICROSTEPS;     // 1600

float topSps = 6000;          // steps/s   (6000 = 225 rpm)
float accel  = 150000;        // steps/s^2
const float START_SPS = 500;  // speed of the first and last step
const float MAX_SPS   = 40000;

// ----- MKS Gen L v1.0 pins (same as RAMPS 1.4) -------------------------------
struct Motor {
  const char *socket;
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
  if (ticks < 50.0f) ticks = 50.0f;
  return (uint16_t)ticks - 1;
}

int32_t readStepsLeft() {
  noInterrupts();
  int32_t v = stepsLeft;
  interrupts();
  return v;
}

// Turn one motor by `steps` (negative = other direction), trapezoid profile.
void turn(const Motor &m, long steps) {
  if (steps == 0) return;
  digitalWrite(m.enPin, LOW);             // make sure the driver is on
  digitalWrite(m.dirPin, steps > 0 ? HIGH : LOW);
  delayMicroseconds(20);
  long total = labs(steps);

  stepPort = portOutputRegister(digitalPinToPort(m.stepPin));
  stepMask = digitalPinToBitMask(m.stepPin);

  float top = min(topSps, MAX_SPS);
  float v = START_SPS, peak = v;
  uint16_t o = ocrFor(v);

  noInterrupts();
  nextOcr = o;
  OCR1A = o;
  TCNT1 = 0;
  stepsLeft = total;
  TIFR1 = (1 << OCF1A);
  TIMSK1 |= (1 << OCIE1A);                // start stepping
  interrupts();

  unsigned long t0 = micros(), last = t0;
  while (true) {
    int32_t left = readStepsLeft();
    if (left <= 0) break;
    unsigned long now = micros();
    if (now - last >= 500) {              // update the speed every 0.5 ms
      float dt = (now - last) * 1e-6f;
      last = now;
      float stopSteps = (v * v - START_SPS * START_SPS) / (2.0f * accel);
      if (left <= stopSteps + 2) v -= accel * dt;   // slow down to stop on the step
      else if (v < top)          v += accel * dt;   // speed up
      if (v > top) v = top;
      if (v < START_SPS) v = START_SPS;
      if (v > peak) peak = v;
      o = ocrFor(v);
      noInterrupts();
      nextOcr = o;
      interrupts();
    }
  }
  TIMSK1 &= ~(1 << OCIE1A);               // stop stepping

  Serial.print(F("OK ")); Serial.print(m.socket); Serial.print(' ');
  Serial.print(steps); Serial.print(F(" steps = "));
  Serial.print(steps * 360.0f / STEPS_PER_REV, 1); Serial.print(F(" deg in "));
  Serial.print((micros() - t0) / 1000.0f, 1); Serial.print(F(" ms, peak "));
  Serial.print((long)peak); Serial.println(F(" steps/s"));
}

// ----- commands -----------------------------------------------------------------
int8_t findMotor(const char *name) {
  for (uint8_t i = 0; i < N_MOTORS; i++)
    if (!strcasecmp(name, motors[i].socket)) return i;
  return -1;
}

void setAll(bool on) {
  for (uint8_t i = 0; i < N_MOTORS; i++) digitalWrite(motors[i].enPin, on ? LOW : HIGH);
}

void printHelp() {
  Serial.println(F("Type: <motor> <steps>   e.g.  X 400   (400 = 90 deg, 1600 = 1 turn, negative = reverse)"));
  Serial.println(F("Motors: X  Y  Z  E0  E1"));
  Serial.println(F("Also: speed <steps/s>   accel <steps/s^2>   on   off   ?"));
  Serial.print(F("Now: speed ")); Serial.print((long)topSps);
  Serial.print(F(" steps/s, accel ")); Serial.print((long)accel); Serial.println(F(" steps/s^2"));
}

void handle(char *line) {
  char *word = strtok(line, " \t");
  if (!word) return;
  char *arg = strtok(NULL, " \t");

  if (!strcmp(word, "?")) { printHelp(); return; }
  if (!strcasecmp(word, "on"))  { setAll(true);  Serial.println(F("OK motors on")); return; }
  if (!strcasecmp(word, "off")) { setAll(false); Serial.println(F("OK motors off")); return; }
  if (!strcasecmp(word, "speed")) {
    float v = arg ? atof(arg) : 0;
    if (v < START_SPS || v > MAX_SPS) { Serial.println(F("ERR speed must be 500-40000")); return; }
    topSps = v; printHelp(); return;
  }
  if (!strcasecmp(word, "accel")) {
    float v = arg ? atof(arg) : 0;
    if (v < 1000 || v > 2000000) { Serial.println(F("ERR accel must be 1000-2000000")); return; }
    accel = v; printHelp(); return;
  }

  int8_t m = findMotor(word);
  if (m < 0) {
    Serial.print(F("ERR unknown motor '")); Serial.print(word);
    Serial.println(F("'. Use X, Y, Z, E0 or E1"));
    return;
  }
  if (!arg) { Serial.println(F("ERR give a number of steps, e.g. X 400")); return; }
  char *end;
  long steps = strtol(arg, &end, 10);
  if (*end != '\0') { Serial.println(F("ERR steps must be a whole number, e.g. 400 or -400")); return; }
  if (labs(steps) > 1000000L) { Serial.println(F("ERR at most 1000000 steps")); return; }
  turn(motors[m], steps);
}

char buf[48];
uint8_t len = 0;

void setup() {
  Serial.begin(115200);
  for (uint8_t i = 0; i < N_MOTORS; i++) {
    pinMode(motors[i].stepPin, OUTPUT);
    pinMode(motors[i].dirPin, OUTPUT);
    pinMode(motors[i].enPin, OUTPUT);
    digitalWrite(motors[i].stepPin, LOW);
  }
  setAll(true);                           // EN is active LOW: drivers on, holding
  TCCR1A = 0;
  TCCR1B = (1 << WGM12) | (1 << CS11);    // Timer1: CTC, prescaler 8
  TIMSK1 = 0;
  delay(500);
  Serial.println(F("MKS motor test: TMC2209 standalone, 1/8 step"));
  printHelp();
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\r') continue;
    if (c == '\n') {
      buf[len] = '\0';
      handle(buf);
      len = 0;
    } else if (len < sizeof(buf) - 1) {
      buf[len++] = c;
    }
  }
}
