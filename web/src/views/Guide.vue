<script setup lang="ts">
import { hosted } from "../api";
import { FLAG_GUIDE } from "../warnings";

// The guide: how to record, what the report shows, how to review, and what
// every flag means. Plain words for players; the details live in the docs.
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Guide</h1>
      <p>Recording a career, reading the report, and fixing the turns it marks.</p>
    </div>
  </div>

  <div class="guide">
    <section class="guide-section">
      <div class="overline">Recording</div>
      <h2>How to Record</h2>
      <ul class="guide-list">
        <li><b>Resolution and language.</b> 1920×1080, English. Other resolutions and languages are not supported.</li>
        <li><b>Log panel.</b> Keep the game's log panel open on the right side. Gains, purchases and events are confirmed from the lines in it.</li>
        <li><b>One file per career.</b> Record from the first turn to the finale in a single mp4, mov, webm or mkv file. A full career is about 1 GB.</li>
        <li><b>No overlays.</b> A webcam, chat or any other overlay on top of the game covers numbers that have to be read.</li>
        <li><b>Skipping animations.</b> Allowed. Skipped results are read again at 60 frames per second.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">The Report</div>
      <h2>What the Report Shows</h2>
      <ul class="guide-list">
        <li><b>Turn strip.</b> One cell per half-month across the three years and the finale, coloured by the action taken. Clicking a cell opens that turn. Scrubbing the video moves the selection with it.</li>
        <li><b>Turn view.</b> The action taken, the stats at the start of the turn with rank letters, performance points, and a log of what happened with the time of each line in the recording.</li>
        <li><b>Analytics.</b> Actions per year, stat growth, the turn each stat reached each rank, training efficiency, races by grade, skills, songs and events.</li>
        <li><b>Unknown values.</b> Every number is read from the screen, worked out from other numbers, or unknown. Worked-out and unknown values are labelled. Unknown is never shown as zero.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Reviewing</div>
      <h2>Marked Turns</h2>
      <p class="muted">For each turn, the starting stats plus every change in the log should equal the next turn's starting stats. A turn where they don't is marked for review.</p>
      <ol class="guide-steps">
        <li><b>Watch the gap.</b> Each gap lists the stat, the amount and the time range. The ▶ chip seeks the video there.</li>
        <li><b>Explain it.</b> Assign the change to an event in the log, add an event the report missed, or enter the number seen.</li>
        <li><b>Check flagged lines.</b> Each flag comes with a description of what to look at. Mark the line Reviewed, or correct its amount.</li>
        <li><b>Save.</b> The verdict at the top turns green once the turn adds up. Corrections are saved separately. The report itself is not changed.</li>
      </ol>
      <p class="muted small">Keyboard: ← and → (or k and j) move between turns on the report page.</p>
    </section>

    <section class="guide-section">
      <div class="overline">Flags</div>
      <h2>Flag Meanings</h2>
      <dl class="guide-flags">
        <template v-for="f in FLAG_GUIDE" :key="f.text">
          <dt><span class="tag small" :class="f.serious ? 'pink' : 'grey'">{{ f.text }}</span></dt>
          <dd>{{ f.advice }}</dd>
        </template>
      </dl>
    </section>

    <section class="guide-section">
      <div class="overline">Privacy</div>
      <h2>Where the Data Is Kept</h2>
      <p v-if="hosted" class="muted">Recordings are uploaded to the service and analyzed there. The original is kept for 90 days so the recording can be analyzed again. The smaller playback copy stays with its report. Deleting a run removes its recording and its reports.</p>
      <p v-else class="muted">Recordings and reports are stored in this computer's data folder. Nothing is uploaded. Deleting a run removes its recording and its reports.</p>
    </section>
  </div>
</template>
