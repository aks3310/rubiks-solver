// ---------------------------------------------------------------------------
// Stepper test - MKS Gen L v1.0 + 5 x BTT TMC2209 v1.3 in STANDALONE mode
// using the AccelStepper library.
//
// Turns each of the 5 motors by 200 full steps (one full turn of a 1.8 deg
// motor), one after the other: X, Y, Z, E0, E1. Then it stops.
// Press the board's reset button, or send 'g' in the Serial Monitor
// (115200 baud), to run it again.
//
// Standalone mode = no UART. The drivers only see STEP / DIR / EN.
// Microstepping comes from the MS1/MS2 jumpers under each driver and the
// current comes from the Vref potentiometer on the driver.
//
// Arduino IDE: Board "Arduino Mega or Mega 2560", Processor "ATmega2560".
// Library    : AccelStepper by Mike McCauley (Library Manager).
// ---------------------------------------------------------------------------
#include <AccelStepper.h>

// ----- settings --------------------------------------------------------------
// Must match the MS1/MS2 jumpers under the drivers (TMC2209 standalone):
//   MS1 off, MS2 off -> 8      MS1 on, MS2 off -> 32
//   MS1 off, MS2 on  -> 64     MS1 on, MS2 on  -> 16
const int  MICROSTEPS = 16;

const long FULL_STEPS = 200;                       // how far each motor turns
const long PULSES     = FULL_STEPS * MICROSTEPS;   // STEP pulses to send

// AccelStepper speeds are in pulses per second. On a 16 MHz ATmega2560 it
// manages about 4000 pulses/s, so keep MAX_SPEED at or below that.
const float MAX_SPEED    = 3200;    // 1 rev/s at 1/16
const float ACCELERATION = 6400;    // pulses/s^2: full speed in 0.5 s

const unsigned int PAUSE_MS = 500;  // pause between motors

// ----- MKS Gen L v1.0 pins (same as RAMPS 1.4) -------------------------------
//                        type                  STEP DIR
AccelStepper stepX (AccelStepper::DRIVER, 54, 55);
AccelStepper stepY (AccelStepper::DRIVER, 60, 61);
AccelStepper stepZ (AccelStepper::DRIVER, 46, 48);
AccelStepper stepE0(AccelStepper::DRIVER, 26, 28);
AccelStepper stepE1(AccelStepper::DRIVER, 36, 34);

AccelStepper *motors[] = {&stepX, &stepY, &stepZ, &stepE0, &stepE1};
const char   *names[]  = {"X", "Y", "Z", "E0", "E1"};
const uint8_t enPins[] = {38, 56, 62, 24, 30};
const uint8_t N_MOTORS = sizeof(motors) / sizeof(motors[0]);

// ----- test ---------------------------------------------------------------------
void runTest() {
  for (uint8_t i = 0; i < N_MOTORS; i++) motors[i]->enableOutputs();
  delay(50);

  for (uint8_t i = 0; i < N_MOTORS; i++) {
    Serial.print(F("Motor "));
    Serial.print(names[i]);
    Serial.print(F(": "));
    Serial.print(FULL_STEPS);
    Serial.print(F(" steps ("));
    Serial.print(PULSES);
    Serial.println(F(" pulses)"));

    motors[i]->move(PULSES);     // relative move; negative = other direction
    motors[i]->runToPosition();  // blocks until done, with accel and decel
    delay(PAUSE_MS);
  }

  Serial.println(F("Done. Send 'g' or press reset to run again."));
  // Motors stay enabled (holding). To let them turn freely by hand, use:
  // for (uint8_t i = 0; i < N_MOTORS; i++) motors[i]->disableOutputs();
}

void setup() {
  Serial.begin(115200);
  for (uint8_t i = 0; i < N_MOTORS; i++) {
    motors[i]->setEnablePin(enPins[i]);
    // invert only EN: the drivers are enabled when EN is LOW
    motors[i]->setPinsInverted(false, false, true);
    motors[i]->setMinPulseWidth(2);         // microseconds; TMC2209 needs > 100 ns
    motors[i]->setMaxSpeed(MAX_SPEED);
    motors[i]->setAcceleration(ACCELERATION);
    motors[i]->disableOutputs();            // off until the test starts
  }
  delay(1000);                              // time to open the Serial Monitor
  Serial.println(F("Stepper test (AccelStepper): MKS Gen L + TMC2209 standalone"));
  runTest();
}

void loop() {
  if (Serial.available() && Serial.read() == 'g') runTest();
}
