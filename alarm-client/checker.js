/**
 * AI Platform — Windows alarm-klient.
 *
 * Poller GET {RAILWAY_URL}/api/alarms/check hvert 30. sekund. Når en alarm
 * udløser, startes Chrome i kiosk-tilstand på morgen-mode-siden.
 *
 * Kør manuelt med:   node checker.js
 * Eller installer som en Task Scheduler-opgave med install.bat.
 */
require("dotenv").config();
const fetch = require("node-fetch");
const { exec } = require("child_process");

const RAILWAY_URL = (process.env.RAILWAY_URL || "").replace(/\/$/, "");
const POLL_INTERVAL_MS = 30 * 1000;
// Undgå at genstarte Chrome flere gange for samme alarm inden for ±1 minut-
// vinduet (poller hvert 30. sekund, så vinduet dækker 3-4 kald).
const COOLDOWN_MS = 5 * 60 * 1000;

if (!RAILWAY_URL) {
  console.error(
    "[alarm-checker] RAILWAY_URL mangler. Kopiér .env.example til .env og udfyld den."
  );
  process.exit(1);
}

let lastTriggered = { id: null, at: 0 };

function launchMorningMode() {
  const url = `${RAILWAY_URL}/?morning=true`;
  // --autoplay-policy=no-user-gesture-required: det er et vækkeur, så
  // ambient-musikken skal kunne spille automatisk uden et klik først.
  const cmd = `start chrome --kiosk --autoplay-policy=no-user-gesture-required "${url}"`;

  console.log(`[alarm-checker] Alarm udløst — starter Chrome i kiosk-tilstand: ${url}`);
  exec(cmd, (err) => {
    if (err) {
      console.error("[alarm-checker] Kunne ikke starte Chrome:", err.message);
      console.error('[alarm-checker] Tjek at "chrome" er i PATH (normalt automatisk via Windows App Paths).');
    }
  });
}

async function checkAlarm() {
  try {
    const res = await fetch(`${RAILWAY_URL}/api/alarms/check`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();

    if (data.triggered && data.alarm) {
      const now = Date.now();
      const triggeredRecently =
        lastTriggered.id === data.alarm.id && now - lastTriggered.at < COOLDOWN_MS;

      if (!triggeredRecently) {
        lastTriggered = { id: data.alarm.id, at: now };
        launchMorningMode();
      }
    }
  } catch (err) {
    console.error("[alarm-checker] Fejl ved kald til /api/alarms/check:", err.message);
  }
}

console.log(
  `[alarm-checker] Starter — tjekker ${RAILWAY_URL}/api/alarms/check hvert ${POLL_INTERVAL_MS / 1000}. sekund`
);
checkAlarm();
setInterval(checkAlarm, POLL_INTERVAL_MS);
