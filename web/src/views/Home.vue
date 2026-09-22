<script setup lang="ts">
import { hosted, type User } from "../api";

// The front page. Short copy, frames from a real recording next to what the
// report makes of them, and the way in: the sign-in screen.
defineProps<{ user: User | null }>();
</script>

<template>
  <section class="hero">
    <div class="hero-copy">
      <h1>Your Career Run, Turn by Turn</h1>
      <p class="lede">Upload a recording of a career. Get every turn's stats with their rank letters, every training, race, purchase and event, and the video at the moment each one happened.</p>
      <div class="row" style="gap: 10px; margin-top: 24px">
        <a v-if="user" class="btn primary big" href="#/runs">Open Runs</a>
        <a v-else class="btn primary big" href="#/signup">Get Started</a>
        <a class="btn big" href="#tour">See What You Get</a>
      </div>
      <p class="muted small" style="margin-top: 14px">{{ hosted ? "Recordings are analyzed on the service's worker. The original is kept for 90 days for a re-analysis." : "Runs on this computer. Nothing leaves it." }}</p>
    </div>
    <div class="hero-shot">
      <img :src="'/shots/game-home.jpg'" alt="A training turn in the recording, with the stat bar" />
      <div class="shot-caption">Senior Year, Early Jun, in the recording. The report reads the same bar: SS+ 1159 speed.</div>
    </div>
  </section>

  <section id="tour" class="tour">
    <div class="tour-row">
      <div class="tour-copy">
        <div class="overline">Replay</div>
        <h2>The Whole Career on One Strip</h2>
        <p>Every half-month of the three years and the finale, coloured by what was chosen. Click a cell or scrub the recording and the turn's log follows. The stats under the video carry the game's rank letters.</p>
      </div>
      <div class="tour-shot">
        <img class="light" :src="'/shots/app-replay-light.jpg'" alt="The report: the season strip, the recording and the turn's log" />
        <img class="dark" :src="'/shots/app-replay-dark.jpg'" alt="The report: the season strip, the recording and the turn's log" />
      </div>
    </div>
    <div class="tour-row flip">
      <div class="tour-copy">
        <div class="overline">From the Recording</div>
        <h2>Read Straight off the Screen</h2>
        <p>The calendar, the stat bar and the popups are read at 4 frames a second, and again at 60 where a number changes. Here a support event pays +3 Speed and +3 Guts. The popup and the outcome line agree, and the report keeps one entry with the second it appeared and the frame behind it.</p>
      </div>
      <div class="tour-shot">
        <img :src="'/shots/game-event.jpg'" alt="A support event in the recording: +3 Speed and +3 Guts, with the same line in the outcome box" />
      </div>
    </div>
    <div class="tour-row">
      <div class="tour-copy">
        <div class="overline">Analytics</div>
        <h2>How the Run Was Played</h2>
        <p>Turn choices per year, growth by year, the turn each stat reached B, A, S, SS and UG, training efficiency, races by grade, skill points and skills, hints, rests and events. Every item opens its turn.</p>
      </div>
      <div class="tour-shot">
        <img class="light" :src="'/shots/app-analytics-light.jpg'" alt="The report's analytics cards" />
        <img class="dark" :src="'/shots/app-analytics-dark.jpg'" alt="The report's analytics cards" />
      </div>
    </div>
    <div class="tour-row flip">
      <div class="tour-copy">
        <div class="overline">From the Recording</div>
        <h2>Races, Placings, Fans</h2>
        <p>Result screens are read for the race, the grade, the placing and the fans gained. A career's races become one list with a badge for each grade.</p>
      </div>
      <div class="tour-shot">
        <img :src="'/shots/game-race.jpg'" alt="A first-place race result in the recording" />
      </div>
    </div>
    <div class="tour-row">
      <div class="tour-copy">
        <div class="overline">From the Recording</div>
        <h2>Lessons, Songs and Skills</h2>
        <p>Grand Concert lessons cost performance points and skills cost skill points. Each purchase is matched to its confirmation and to the balance before and after it, so the report lists what was bought, what it cost and what it gave, and never a purchase that was only browsed.</p>
      </div>
      <div class="tour-shot">
        <img :src="'/shots/game-lessons.jpg'" alt="The Grand Concert lesson menu in the recording, with performance points and the songs and classes learned in the log" />
      </div>
    </div>
  </section>

  <section class="steps-wrap">
    <h2>Three Steps</h2>
    <ol class="steps">
      <li><b>Record</b> at 1080p in landscape.</li>
      <li><b>Upload</b> the file, about 1 GB for a career.</li>
      <li><b>Read</b> the report beside the recording.</li>
    </ol>
  </section>

  <section id="start" class="start">
    <div v-if="user" class="start-card signed">
      <h2>You Are Signed In</h2>
      <p class="muted">Your uploads and reports are under Runs.</p>
      <a class="btn primary big" href="#/runs">Open Runs</a>
    </div>
    <template v-else>
      <div class="start-copy">
        <h2>Get Started</h2>
        <p class="muted">{{ hosted ? "Create an account, upload a recording, and open the report when it lands." : "Create an account on this machine, upload a recording, and open the report when it lands." }}</p>
      </div>
      <div class="start-card">
        <a class="btn primary big" href="#/signup">Create an Account</a>
        <a class="btn big" style="margin-left: 8px" href="#/signin">Sign In</a>
      </div>
    </template>
  </section>
</template>
