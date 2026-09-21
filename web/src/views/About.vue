<script setup lang="ts">
import { hosted } from "../api";
import { desktop, openExternal } from "../mode";
import { NOTICES_URL, REPO_URL } from "../legal";
// What this application is, in a page: the parts, what the analyzer reads,
// where the data lives, and where the details are.
</script>

<template>
  <div class="page-head">
    <div>
      <h1>About Tracen Replay</h1>
      <p>Turns a recording of an Umamusume: Pretty Derby career into a turn-by-turn report that can be checked against the video.</p>
    </div>
  </div>

  <div class="guide">
    <section class="guide-section">
      <div class="overline">What It Does</div>
      <h2>How It Works</h2>
      <ul class="guide-list">
        <li><b>Reading.</b> The recording is sampled at 4 frames per second and each frame is read with OCR. Stretches where a number changed too fast are read again at 60.</li>
        <li><b>Timestamps.</b> Every stat, gain, purchase, race and event keeps the time in the recording it was read from, so it can be checked by seeking there.</li>
        <li><b>Accounting.</b> For each turn, the starting stats plus the changes read must equal the next turn's starting stats. A turn that doesn't add up is marked for review. Missing values are not filled in with guesses.</li>
        <li><b>Corrections.</b> Corrections made in a review are stored separately from the report and checked by the same accounting. The report is never rewritten.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Components</div>
      <h2>What It Is Made Of</h2>
      <ul class="guide-list">
        <li><b>Service.</b> Serves these pages and stores uploads, analyses and reports. Analyses run one at a time.</li>
        <li><b>Analyzer.</b> Started by the service for each recording. It captures frames, runs OCR and the re-reads, then assembles turns, events and the accounting.</li>
        <li><b>Client.</b> These pages. They display the report's timeline and compute no values of their own.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Scope</div>
      <h2>What Is Read</h2>
      <ul class="guide-list">
        <li><b>Read.</b> The calendar and stat bar, training previews and results, support events and choices, award popups, race results, the Grand Concert lesson menu and receipts, concert bonuses, skill purchases, energy, mood and friendship receipts, rests and outings.</li>
        <li><b>Not Read.</b> Story text, supporter introductions, and anything without a number or a named effect. Recordings other than the English game at 1080p with the log panel open are not supported.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Privacy</div>
      <h2>Where the Data Is Kept</h2>
      <p v-if="hosted" class="muted">Recordings are uploaded to the service and analyzed there. The original is kept for 90 days so the recording can be analyzed again. The smaller playback copy stays with its report.</p>
      <p v-else class="muted">Recordings and reports are stored in this computer's data folder, and the service accepts connections from this computer only. Nothing is uploaded. A released build asks GitHub once a day for the newest release, sending its own version and nothing else. {{ desktop ? "Update Check in Settings turns that off." : "The -update-check=false flag turns that off." }}</p>
      <p class="muted">Recording requirements and the review steps are in the <a href="#/guide">Guide</a>.</p>
    </section>

    <section class="guide-section">
      <div class="overline">Project</div>
      <h2>Source and License</h2>
      <p class="muted">Source code: <a :href="REPO_URL" target="_blank" rel="noopener noreferrer" @click="openExternal($event, REPO_URL)">github.com/andy-dam/tracen-replay</a>. Licensed under PolyForm Noncommercial 1.0.0. Third-party components and their licenses are in the <a :href="NOTICES_URL" target="_blank" rel="noopener noreferrer" @click="openExternal($event, NOTICES_URL)">Third-Party Notices</a>. Not affiliated with Cygames, Inc. See the <a href="#/privacy">Privacy Policy</a> and the <a href="#/terms">Terms of Use</a>.</p>
    </section>
  </div>
</template>
