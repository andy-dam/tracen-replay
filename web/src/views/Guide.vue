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
      <p>How to record a career, read the report, and fix the turns it marks for review.</p>
    </div>
  </div>

  <div class="guide">
    <section class="guide-section">
      <div class="overline">Recording</div>
      <h2>How to Record</h2>
      <ul class="guide-list">
        <li><b>PC.</b> Record the English version of the game full screen in 16:9. Anything from 1280×720 to 3840×2160 works, but below 1920×1080 more of the small text is missed. Only the game on the left is read, not the side panel.</li>
        <li><b>Phone or Tablet.</b> Use the device's own screen recorder with the game in portrait. A portrait recording shown inside a landscape video also works, as long as the area around the game stays still, like a fixed background or black bars. If the surroundings move as much as the game does, the game can't be found.</li>
        <li><b>One File per Career.</b> Record from the first turn to the finale in a single file. MP4, MOV, WebM and MKV all work. A full career is usually 1 to 4 GB.</li>
        <li><b>No Overlays.</b> A webcam, chat or anything else on top of the game can cover numbers the report needs.</li>
        <li><b>Skipping Animations.</b> Skipping is fine. Results that flash by are read again frame by frame.</li>
      </ul>
      <p class="muted small" style="margin-top: 10px">Other languages and screen shapes aren't supported.</p>
    </section>

    <section class="guide-section">
      <div class="overline">Analyzing</div>
      <h2>Pausing an Analysis</h2>
      <p>An analysis can be paused from the Runs page or from its own page. Pausing keeps everything read so far. Resuming puts the analysis back in the queue, and it continues where it stopped. If the app or the service stops while an analysis is running, the analysis is paused, not lost.</p>
      <p v-if="hosted">A paused analysis keeps its progress for 24 hours. After that the progress is deleted, but the recording stays and can be analyzed again from the start.</p>
      <p v-else>A paused analysis keeps its progress until it is resumed or cancelled. The Paused Analyses setting can delete that progress after a set number of days.</p>
      <p>Cancel stops the analysis and deletes its progress.</p>
    </section>

    <section class="guide-section">
      <div class="overline">The Report</div>
      <h2>What the Report Shows</h2>
      <ul class="guide-list">
        <li><b>Timeline.</b> One cell for each half-month of the three years, plus the finale, colored by what was done that turn. Click a cell to open its turn. Scrubbing the video moves the selection along with it.</li>
        <li><b>Turn.</b> The action taken, the stats at the start of the turn with their rank letters, performance points, and a log of everything that happened, with the time of each line in the video.</li>
        <li><b>How the Run Was Played.</b> A summary of the whole career below the turn: actions per year, stat growth, the turn each stat reached each rank, training efficiency, races by grade, skills, songs and events.</li>
        <li><b>Unknown Values.</b> Every number is either read from the screen, worked out from other numbers, or unknown. Worked-out and unknown values are labeled, and an unknown value is never shown as zero.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Reviewing</div>
      <h2>Checking Marked Turns</h2>
      <p>The stats at the start of a turn, plus every change in its log, should equal the stats at the start of the next turn. When they don't, the turn is marked for review. To check one:</p>
      <ol class="guide-steps">
        <li><b>Find the Gap.</b> Each gap shows the stat, the amount and when it happened. The ▶ button jumps the video there.</li>
        <li><b>Explain It.</b> Choose Belongs to an Event to put the change on a line in the log, Missed Event to add an event the report didn't catch, or Enter Amount to type in the number you saw. Choose Didn't Happen if the report misread a number.</li>
        <li><b>Check Flagged Lines.</b> Each flag says what to look for. Press Looks Right if the line is correct, or fix its amount.</li>
        <li><b>Save.</b> The badge at the top turns green once the turn adds up. Corrections are saved separately, and the report itself doesn't change.</li>
      </ol>
      <p class="muted small">On the report page, the ← and → keys (or k and j) move between turns.</p>
    </section>

    <section class="guide-section">
      <div class="overline">Flags</div>
      <h2>What the Flags Mean</h2>
      <p>Pink flags need a look. Gray flags are notes.</p>
      <dl class="guide-flags">
        <template v-for="f in FLAG_GUIDE" :key="f.text">
          <dt><span class="tag small" :class="f.serious ? 'pink' : 'grey'">{{ titleCase(f.text) }}</span></dt>
          <dd>{{ f.advice }}</dd>
        </template>
      </dl>
    </section>

    <section class="guide-section">
      <div class="overline">Privacy</div>
      <h2>Where Your Data Is Kept</h2>
      <p v-if="hosted">Recordings are uploaded to this website and analyzed on its servers. The original file is kept for 90 days so it can be analyzed again, and a smaller copy for playback stays with its report. Deleting a run removes its recording and reports.</p>
      <p v-else>Recordings and reports are stored in this computer's data folder and are never uploaded. Deleting a run removes its recording and reports.</p>
    </section>
  </div>
</template>
