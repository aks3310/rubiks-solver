// ---------------------------------------------------------------------------
// Rubik's cube robot firmware - configuration
// Board : MKS Gen L v1.0 (ATmega2560, RAMPS-compatible pin numbers)
// Drivers: 5 x BIGTREETECH TMC2209 v1.2/v1.3 in UART mode
// Motors : 5 x JK42HS48-1684 (1.68 A/phase rated)
// ---------------------------------------------------------------------------
#pragma once

// ----- motion --------------------------------------------------------------
#define FULL_STEPS_PER_REV   200      // 1.8 degree motors
// 1/8 halves the step rate the ATmega2560 must produce at high speed
// (10,000 steps/s = 375 rpm) while still giving 0.225 degree resolution.
#define MICROSTEPS           8        // set over UART (jumpers are not used)
#define QUARTER_STEPS        (FULL_STEPS_PER_REV * MICROSTEPS / 4)   // 400

// Starting point for tuning, NOT the 2 s target. Estimated ~0.11 s per
// quarter turn and ~0.17 s per half turn. Raise with SPEED until turns
// start to skip, then back off ~15 %. (Goal: ~10000 sps, ~300000 sps^2.)
#define DEFAULT_MAX_SPS      3000     // steps/s  (6000 = 225 rpm at 1/8)
#define DEFAULT_ACCEL        150000   // steps/s^2
#define START_SPS            600      // speed of the first and last step
#define JOG_SPS              600
#define DEFAULT_SETTLE_US    2000     // pause after every move (SETTLE command)
#define RAMP_TABLE_MAX       600      // max acceleration steps kept in the delay table
// Overshoot: every MOV turn goes this far past its target in the direction
// of travel, then comes back. 0 = off. The Pi sets it with OVERSHOOT <deg>
// (from config.toml) every time it connects.
#define DEFAULT_OVERSHOOT_DEG 0
#define MAX_OVERSHOOT_DEG     30
#define OVERSHOOT_PAUSE_US    0       // pause at the far point before coming back
#define MAX_SHAKE_DEG         15      // largest SHAKE amplitude accepted

// ----- TMC2209 -------------------------------------------------------------
#define R_SENSE              0.11f    // BTT TMC2209 v1.2/v1.3 sense resistor (check your module)
#define RUN_CURRENT_MA       1350     // rms, 80 % of the motor's 1.68 A rating; watch driver/motor heat
#define HOLD_MULTIPLIER      0.5f     // hold current = 50 % of run current
#define USE_SPREADCYCLE      true     // more torque at speed than StealthChop
#define TMC_SW_BAUD          19200    // software-serial drivers
#define TMC_HW_BAUD          115200   // hardware-serial driver (Serial2)

// ----- host link -----------------------------------------------------------
#define HOST_BAUD            115200

// ----- pins ----------------------------------------------------------------
// Which socket drives which face is decided by the STEP/DIR/EN numbers below.
// Socket  Face  STEP DIR EN   UART used for that face (TX -> 1k -> PDN_UART)
// X       D      54  55  38   TX2 D16, RX2 D17 (EXP1)  hardware Serial2
// Y       L      60  61  56   TX D59, RX D64  (AUX-2)  software serial
// Z       F      46  48  62   TX D42, RX D65  (AUX-2)  software serial
// E0      B      26  28  24   TX D44, RX D66  (AUX-2)  software serial
// E1      R      36  34  30   TX D40, RX D63  (AUX-2)  software serial
// In standalone mode (no UART wires) the UART pins are unused. If UART is
// ever wired, each pair must go to the driver in the socket of that face.
// The AUX-2 pairs are the same ones Marlin uses for RAMPS-style boards.
// Keep pi/main.py's SOCKET map and docs/wiring.md in step with this table.

#define D_STEP 54
#define D_DIR  55
#define D_EN   38
#define D_SERIAL Serial2

#define L_STEP 60
#define L_DIR  61
#define L_EN   56
#define L_TX   59
#define L_RX   64

#define F_STEP 46
#define F_DIR  48
#define F_EN   62
#define F_TX   42
#define F_RX   65

#define B_STEP 26
#define B_DIR  28
#define B_EN   24
#define B_TX   44
#define B_RX   66

#define R_STEP 36
#define R_DIR  34
#define R_EN   30
#define R_TX   40
#define R_RX   63

// ----- direction -----------------------------------------------------------
// A move "F" must turn the front face CLOCKWISE as seen from the front.
// Test each face with "MOV F" etc. and flip the matching flag if it turns
// the wrong way.
#define F_INVERT false
#define R_INVERT false
#define B_INVERT false
#define L_INVERT false
#define D_INVERT false
