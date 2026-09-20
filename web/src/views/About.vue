<script setup lang="ts">
import { hosted } from "../api";
// What this application is, in a page: the parts, what the analyzer reads,
// where the data lives, and where the details are.
</script>

<template>
  <div class="page-head">
    <div>
      <h1>About Tracen Replay</h1>
      <p>A local application that turns a recording of an Umamusume: Pretty Derby career into a turn-by-turn report you can check against the video.</p>
    </div>
  </div>

  <div class="guide">
    <section class="guide-section">
      <div class="overline">What It Does</div>
      <h2>From a Recording to a Career Ledger</h2>
      <ul class="guide-list">
        <li><b>Reads the screen.</b> Every frame of the recording is sampled at four frames a second and read with OCR; where a number went by too fast, the frames around it are read again at sixty.</li>
        <li><b>Keeps the evidence.</b> Every stat, gain, purchase, race and event carries the second of the recording it was read from, so any line can be checked by seeking there.</li>
        <li><b>Balances the books.</b> The stats at the start of each turn plus everything read in it must equal the stats at the start of the next. Where they do not, the report says so and asks you to look, rather than guessing.</li>
        <li><b>Keeps your answers apart.</b> What you fill in on a review is stored beside the report and checked the same way; the report itself is never rewritten.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">The Parts</div>
      <h2>One Binary, One Worker</h2>
      <ul class="guide-list">
        <li><b>The service.</b> A single program that serves this page, keeps accounts, uploads, analyses and reports in one local database, and runs one analysis at a time.</li>
        <li><b>The analyzer.</b> The worker the service starts for each recording: frame capture, OCR, the rereads, and the assembly of turns, events and the accounting.</li>
        <li><b>This client.</b> The pages you are looking at, built from the report's timeline only; it never derives a value of its own.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Scope</div>
      <h2>What Is and Is Not Read</h2>
      <ul class="guide-list">
        <li><b>Read.</b> The calendar and stat bar, training previews and results, support events and choices, award popups, race results, the Grand Concert lesson menu and receipts, concert bonuses, skill purchases, energy, mood and friendship receipts, rests and outings.</li>
        <li><b>Not read.</b> Story text, supporter introductions, anything without a number or a named effect, and any layout other than the English 1080p game with the log panel open.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Privacy</div>
      <h2>Local by Design</h2>
      <p class="muted">{{ hosted ? "Recordings are analyzed on the service's worker; originals are kept for 90 days so a report can be made again, and the playback copy stays with its report." : "Recordings, reports and accounts live in the data folder on this machine and the service listens on this machine only. Nothing is uploaded anywhere." }} See the <a href="#/guide">Guide</a> for recording tips and how reviewing works.</p>
    </section>
  </div>
</template>
