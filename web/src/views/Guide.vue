<script setup lang="ts">
import { FLAG_GUIDE } from "../warnings";

// The guide: how to record, what the report shows, how to review, and what
// every flag means. Plain words for players; the details live in the docs.
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Guide</h1>
      <p>How to record a career, what you get back, and how to settle what the report asks you to check.</p>
    </div>
  </div>

  <div class="guide">
    <section class="guide-section">
      <div class="overline">Recording</div>
      <h2>Record the Career So It Can Be Read</h2>
      <ul class="guide-list">
        <li><b>1080p, English.</b> The reader knows the game's 1920×1080 layout; other sizes and languages are not read.</li>
        <li><b>Log panel open.</b> Keep the game's log panel visible on the right; the receipts in it are how gains, purchases and events are confirmed.</li>
        <li><b>One career per file.</b> From the first turn to the finale, in one recording. mp4, mov, webm or mkv; a full career is about 1 GB.</li>
        <li><b>Nothing on top.</b> Overlays, webcams or chat windows over the game hide numbers the reader needs.</li>
        <li><b>Skipping is fine.</b> Result animations can be skipped; the reader looks again at 60 frames a second where a number went by too fast.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">The Report</div>
      <h2>What You Get Back</h2>
      <ul class="guide-list">
        <li><b>The strip.</b> Every half-month of the three years and the finale, coloured by what was chosen. Click a cell, or scrub the recording and the turn follows.</li>
        <li><b>The turn.</b> What was chosen, the stats at the start with their rank letters, the performance points, and the log of everything that happened, each line with its second in the recording.</li>
        <li><b>The analytics.</b> Turn choices per year, growth, the turn each stat reached a rank, training efficiency, races by grade, skills, songs and events.</li>
        <li><b>Nothing invented.</b> A number is either read from the screen or marked as worked out or unknown. Unknown is never shown as zero.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Reviewing</div>
      <h2>When the Report Asks You to Look</h2>
      <p class="muted">The stats at the start of a turn plus everything the report saw should equal the stats at the start of the next turn. Where they do not, the turn is marked and the review screen walks you through it.</p>
      <ol class="guide-steps">
        <li><b>Seek.</b> Each gap says what changed and between which seconds. Press its ▶ chip and watch.</li>
        <li><b>Explain.</b> Say whether the change belongs to an event already in the log, to an event the report missed, or just give the number you saw.</li>
        <li><b>Check the flags.</b> Each flagged line says what the flag means and what to look at. Tick Reviewed when it looks right, or fix the amount.</li>
        <li><b>Save.</b> The verdict at the top turns green when it adds up. Your edits are kept beside the report and never change the report itself.</li>
      </ol>
      <p class="muted small">Keyboard on the report page: ← and → (or k and j) move between turns.</p>
    </section>

    <section class="guide-section">
      <div class="overline">Flags</div>
      <h2>What Each Flag Means</h2>
      <dl class="guide-flags">
        <template v-for="f in FLAG_GUIDE" :key="f.text">
          <dt><span class="tag small" :class="f.serious ? 'pink' : 'grey'">{{ f.text }}</span></dt>
          <dd>{{ f.advice }}</dd>
        </template>
      </dl>
    </section>

    <section class="guide-section">
      <div class="overline">Privacy</div>
      <h2>Everything Stays Here</h2>
      <p class="muted">Recordings, reports and accounts live in this machine's data folder. Nothing is sent anywhere. Deleting a run removes its recording and its reports.</p>
    </section>
  </div>
</template>
