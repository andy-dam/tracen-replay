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
      <p>Tracen Replay is a free, fan-made tool that turns a screen recording of an Umamusume: Pretty Derby career into a turn-by-turn report you can check against the video.</p>
    </div>
  </div>

  <div class="guide">
    <section class="guide-section">
      <div class="overline">Analysis</div>
      <h2>How It Works</h2>
      <p>The analyzer samples the recording four times a second and reads each frame with text recognition. Where a number changes too quickly to catch that way, it goes back and reads every frame.</p>
      <p>Every stat, gain, purchase, race and event in the report keeps the time in the video it was read from, so any line can be checked by jumping to that moment.</p>
      <p>Each turn is also checked against the next one. The stats at the start of a turn, plus every change read during it, should equal the stats at the start of the next turn. When they don't, the turn is marked for review. Values that were never seen are left blank, not guessed.</p>
      <p>Corrections made in a review are saved separately and checked the same way. The original report is never rewritten.</p>
    </section>

    <section class="guide-section">
      <div class="overline">Components</div>
      <h2>What It's Made Of</h2>
      <p>Tracen Replay has three parts. The service serves these pages and stores the uploads, analyses and reports. The analyzer is started by the service for each recording. It extracts the frames, reads them, and builds the turns, events and checks. The client is these pages, which display the report without computing any values of their own.</p>
    </section>

    <section class="guide-section">
      <div class="overline">Scope</div>
      <h2>What It Reads</h2>
      <p>Tracen Replay reads the calendar and stat bar, training previews and results, support events and their choices, award popups, race results, Grand Concert lessons and their receipts, concert bonuses, skill purchases, changes to energy, mood and friendship, rests and outings.</p>
      <p>It doesn't read story text, supporter introductions, the PC client's side panel, or anything without a number or a named effect. Only the English version of the game is supported, recorded in 16:9 landscape from 720p to 4K, or in portrait on a phone or tablet.</p>
    </section>

    <section class="guide-section">
      <div class="overline">Privacy</div>
      <h2>Where Your Data Is Kept</h2>
      <p v-if="hosted">Recordings are uploaded to this website and analyzed on its servers. The original file is kept for 90 days so it can be analyzed again, and a smaller copy for playback stays with its report. The desktop app keeps everything on your own computer instead.</p>
      <p v-else>Recordings and reports are stored in this computer's data folder, and the service only accepts connections from this computer. Nothing is uploaded. Released builds check GitHub once a day for a newer release, sending only the installed version. {{ desktop ? "Update Check in Settings turns this off." : "The -update-check=false flag turns this off." }}</p>
      <p>The <a href="#/guide">Guide</a> covers how to record a career and how to review a report.</p>
    </section>

    <section class="guide-section">
      <div class="overline">Project</div>
      <h2>Source Code and License</h2>
      <p>The source code is on GitHub at <a :href="REPO_URL" target="_blank" rel="noopener noreferrer" @click="openExternal($event, REPO_URL)">github.com/andy-dam/tracen-replay</a>, under the PolyForm Noncommercial 1.0.0 license. Third-party components and their licenses are listed in the <a :href="NOTICES_URL" target="_blank" rel="noopener noreferrer" @click="openExternal($event, NOTICES_URL)">Third-Party Notices</a>. Tracen Replay is a hobby project and is not affiliated with Cygames, Inc. See also the <a href="#/privacy">Privacy Policy</a> and the <a href="#/terms">Terms of Use</a>.</p>
    </section>
  </div>
</template>
