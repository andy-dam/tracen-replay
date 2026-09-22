<script setup lang="ts">
import { hosted } from "../api";
import { titleCase } from "../format";
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
        <li><b>Resolution and Language.</b> 1920×1080, English. Other resolutions and languages are not supported.</li>
        <li><b>Game Window.</b> Full screen and landscape. Only the gameplay pane on the left is read, not the side panel.</li>
        <li><b>One File per Career.</b> Record from the first turn to the finale in a single mp4, mov, webm or mkv file. A full career is about 1 GB.</li>
        <li><b>No Overlays.</b> A webcam, chat or any other overlay on top of the game covers numbers that have to be read.</li>
        <li><b>Skipping Animations.</b> Allowed. Skipped results are read again at 60 frames per second.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Analyzing</div>
      <h2>Pausing an Analysis</h2>
      <ul class="guide-list">
        <li><b>Pause.</b> Stops the analysis and keeps the frames it has read. The buttons are on the Runs page and on the analysis page.</li>
        <li><b>Resume.</b> Puts the analysis back in the queue. Frames already read are not read again.</li>
        <li><b>Closing the Application.</b> An analysis that was running when the application or the service stopped is paused, not lost. Resume continues it.</li>
        <li v-if="hosted"><b>Kept for 24 Hours.</b> The progress of a paused analysis is deleted 24 hours after the pause. The recording stays and can be analyzed from the start.</li>
        <li v-else><b>Kept Until Resumed.</b> A paused analysis keeps its progress until it is resumed or cancelled. Settings has an option that deletes the progress of analyses left paused for a number of days.</li>
        <li><b>Cancel.</b> Stops the analysis and deletes its progress.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">The Report</div>
      <h2>What the Report Shows</h2>
      <ul class="guide-list">
        <li><b>Turn Strip.</b> One cell per half-month across the three years and the finale, coloured by the action taken. Clicking a cell opens that turn. Scrubbing the video moves the selection with it.</li>
        <li><b>Turn View.</b> The action taken, the stats at the start of the turn with rank letters, performance points, and a log of what happened with the time of each line in the recording.</li>
        <li><b>Analytics.</b> Actions per year, stat growth, the turn each stat reached each rank, training efficiency, races by grade, skills, songs and events.</li>
        <li><b>Unknown Values.</b> Every number is read from the screen, worked out from other numbers, or unknown. Worked-out and unknown values are labelled. Unknown is never shown as zero.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Reviewing</div>
      <h2>Marked Turns</h2>
      <p class="muted">For each turn, the starting stats plus every change in the log should equal the next turn's starting stats. A turn where they don't is marked for review.</p>
      <ol class="guide-steps">
        <li><b>Watch the Gap.</b> Each gap lists the stat, the amount and the time range. The ▶ chip seeks the video there.</li>
        <li><b>Explain It.</b> Assign the change to an event in the log, add an event the report missed, or enter the number seen. Didn't Happen says the report misread a number and sets the gap aside.</li>
        <li><b>Check Flagged Lines.</b> Each flag comes with a description of what to look at. Press Looks Right on the line, or correct its amount.</li>
        <li><b>Save.</b> The verdict at the top turns green once the turn adds up. Corrections are saved separately. The report itself is not changed.</li>
      </ol>
      <p class="muted small">Keyboard: ← and → (or k and j) move between turns on the report page.</p>
    </section>

    <section class="guide-section">
      <div class="overline">Flags</div>
      <h2>Flag Meanings</h2>
      <dl class="guide-flags">
        <template v-for="f in FLAG_GUIDE" :key="f.text">
          <dt><span class="tag small" :class="f.serious ? 'pink' : 'grey'">{{ titleCase(f.text) }}</span></dt>
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
