<script setup lang="ts">
import { version } from "../../package.json";
import { hosted, type User } from "../api";
import { REPO_URL } from "../legal";

// The front page. Short copy, frames from a real recording next to what the
// report makes of them, and the two ways in: an account on this service, or
// the desktop application.
defineProps<{ user: User | null }>();

// The desktop application of the release this client belongs to, under the
// file names the release workflow gives it.
const tag = `v${version}`;
const files = `${REPO_URL}/releases/download/${tag}/`;
const windowsDownload = `${files}TracenReplay-Setup-${tag}.exe`;
const macDownload = `${files}TracenReplay-macos-arm64-${tag}.dmg`;
const releasePage = `${REPO_URL}/releases/tag/${tag}`;
</script>

<template>
  <section class="hero">
    <div class="hero-copy">
      <h1>Turn-by-Turn Reports for Umamusume Careers</h1>
      <p class="lede">Upload a screen recording of an Umamusume: Pretty Derby career. Tracen Replay reads it and lays out every turn: the stats, the training or race chosen, each event and purchase, and the exact moment in the video it happened.</p>
      <div class="row" style="gap: 10px; margin-top: 24px">
        <a v-if="user" class="btn primary big" href="#/runs">Open Runs</a>
        <a v-else class="btn primary big" href="#/signup">Get Started</a>
        <a v-if="hosted" class="btn big" href="#download">Download the App</a>
      </div>
      <p v-if="!hosted" class="muted small" style="margin-top: 14px">Everything runs on this computer. Nothing is uploaded.</p>
    </div>
    <div class="hero-shot">
      <img :src="'/shots/game-home.jpg'" alt="A training turn in the recording, with the stat bar" />
      <div class="shot-caption">From the recording: a training turn in Senior Year, Early June. The report reads the same stat bar, including Speed at SS+ 1159.</div>
    </div>
  </section>

  <section id="tour" class="tour">
    <div class="tour-row">
      <div class="tour-copy">
        <div class="overline">In the Report</div>
        <h2>The Whole Career in One Timeline</h2>
        <p>The timeline covers every half-month of the three years and the finale, colored by what was done on each turn. Click a turn or scrub through the video, and the turn's log follows along. The stats under the video show the same rank letters as the game.</p>
      </div>
      <div class="tour-shot">
        <img class="light" :src="'/shots/app-replay-light.jpg'" alt="The report: the season strip, the recording and the turn's log" />
        <img class="dark" :src="'/shots/app-replay-dark.jpg'" alt="The report: the season strip, the recording and the turn's log" />
      </div>
    </div>
    <div class="tour-row flip">
      <div class="tour-copy">
        <div class="overline">From the Recording</div>
        <h2>Read from the Screen</h2>
        <p>The calendar, the stat bar and the popups are read from the video itself: four frames every second, and every frame wherever a number changes quickly. In this support event, the popup and the outcome box both show +3 Speed and +3 Guts, so the report keeps one entry, with the time it appeared and the frame it was read from.</p>
      </div>
      <div class="tour-shot">
        <img :src="'/shots/game-event.jpg'" alt="A support event in the recording: +3 Speed and +3 Guts, with the same line in the outcome box" />
      </div>
    </div>
    <div class="tour-row">
      <div class="tour-copy">
        <div class="overline">In the Report</div>
        <h2>How the Run Was Played</h2>
        <p>Below the timeline, the report sums up the whole run: how the turns were spent each year, how each stat grew and when it reached B, A, S, SS and UG, training efficiency, races, skills, rests and hints. Click any item to open its turn.</p>
      </div>
      <div class="tour-shot">
        <img class="light" :src="'/shots/app-analytics-light.jpg'" alt="The report's analytics cards" />
        <img class="dark" :src="'/shots/app-analytics-dark.jpg'" alt="The report's analytics cards" />
      </div>
    </div>
    <div class="tour-row flip">
      <div class="tour-copy">
        <div class="overline">From the Recording</div>
        <h2>Races and Results</h2>
        <p>Each race result screen is read for the race, its grade, the placing and the fans gained. All of a career's races are collected in one list, with a badge for each grade.</p>
      </div>
      <div class="tour-shot">
        <img :src="'/shots/game-race.jpg'" alt="A first-place race result in the recording" />
      </div>
    </div>
    <div class="tour-row">
      <div class="tour-copy">
        <div class="overline">From the Recording</div>
        <h2>Lessons, Songs and Skills</h2>
        <p>Grand Concert lessons cost performance points, and skills cost skill points. Each purchase is matched to its confirmation and to the points before and after it, so the report lists only what was actually bought, with what it cost and what it gave. Anything that was only looked at in a menu is left out.</p>
      </div>
      <div class="tour-shot">
        <img :src="'/shots/game-lessons.jpg'" alt="The Grand Concert lesson menu in the recording, with performance points and the songs and classes learned in the log" />
      </div>
    </div>
  </section>

  <section class="steps-wrap">
    <h2>How It Works</h2>
    <ol class="steps">
      <li><b>Record</b> a career from the first turn to the finale, on PC at 1080p or on a phone or tablet.</li>
      <li><b>Upload</b> the video. A full career is usually 1 to 4 GB.</li>
      <li><b>Review</b> the report next to the video once the analysis is done.</li>
    </ol>
  </section>

  <section id="start" class="start">
    <div v-if="user" class="start-card signed">
      <h2>Welcome Back</h2>
      <p class="muted">Your recordings and reports are on the Runs page.</p>
      <a class="btn primary big" href="#/runs">Open Runs</a>
    </div>
    <template v-else>
      <div class="start-copy">
        <h2>Get Started</h2>
        <p class="muted">{{ hosted ? "Create a free account and upload a recording. The report appears under Runs when the analysis is done." : "Create an account on this computer and upload a recording. The report appears under Runs when the analysis is done." }}</p>
        <p v-if="hosted" class="muted small">Analyses on this website run one at a time on a shared server with no graphics card, so they take much longer than the desktop app on a computer that has one.</p>
      </div>
      <div class="start-card">
        <a class="btn primary big" href="#/signup">Create an Account</a>
        <a class="btn big" href="#/signin">Sign In</a>
      </div>
    </template>
  </section>

  <section v-if="hosted" id="download" class="start">
    <div class="start-copy">
      <h2>Download the Desktop App</h2>
      <p class="muted">The desktop app is free and runs the same analysis on your own computer. Your recordings never leave it, and no account is needed. With a graphics card, or on a Mac with Apple Silicon, it's also much faster than this website.</p>
    </div>
    <div class="start-card">
      <a class="btn primary big" :href="windowsDownload">Download for Windows</a>
      <a class="btn big" :href="macDownload">Download for Mac</a>
      <p class="muted small">Windows 10 or 11, or a Mac with Apple Silicon (M1 or later). The apps aren't code-signed yet, so Windows shows a warning the first time and macOS needs one Terminal command before it will open the app. The <a :href="releasePage" target="_blank" rel="noopener noreferrer">release notes</a> have the steps.</p>
    </div>
  </section>
</template>
