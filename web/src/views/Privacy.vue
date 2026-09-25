<script setup lang="ts">
import { ref } from "vue";
import { api, ApiError, hosted, type User } from "../api";
import { desktop, openExternal } from "../mode";
import { CONTACT_EMAIL, EFFECTIVE, ISSUES_URL, REPO_URL } from "../legal";

// What the service stores, where, for how long, and how it is deleted. The
// website and the desktop application differ in almost every answer, so each
// reads its own version.
const props = defineProps<{ user: User | null }>();
const emit = defineEmits<{ deleted: [] }>();

const password = ref("");
const error = ref("");
const busy = ref(false);
const asking = ref(false);

async function deleteAccount() {
  error.value = "";
  busy.value = true;
  try {
    await api.deleteAccount(password.value);
    emit("deleted");
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Privacy Policy</h1>
      <p>What Tracen Replay stores, where it's kept, and how to delete it. In effect since {{ EFFECTIVE }}.</p>
    </div>
  </div>

  <div v-if="!hosted" class="guide">
    <section class="guide-section">
      <div class="overline">This Computer</div>
      <h2>{{ desktop ? "The Desktop Application" : "A Service Run From Source" }}</h2>
      <ul class="guide-list">
        <li><b>Storage.</b> Recordings, reports, corrections and settings are saved as files in this computer's data folder. Nothing is uploaded, and the service only accepts connections from this computer.</li>
        <li><b>No Tracking.</b> There are no analytics, ads, telemetry or crash reports.</li>
        <li><b>Update Check.</b> Released builds ask GitHub once a day whether there's a newer release. The request includes the installed version and nothing else. GitHub sees the computer's IP address, as any website would. {{ desktop ? "Update Check in Settings turns this off." : "The -update-check=false flag turns this off." }}</li>
        <li><b>Deleting.</b> Deleting a run removes its recording and reports. Deleting the data folder removes everything.</li>
      </ul>
    </section>
    <section class="guide-section">
      <div class="overline">The Website</div>
      <h2>The Hosted Service</h2>
      <p>The hosted website stores uploads on its servers. Its own Privacy Policy is on its Privacy page.</p>
    </section>
  </div>

  <div v-else class="guide">
    <section class="guide-section">
      <div class="overline">Operator</div>
      <h2>Who Runs the Service</h2>
      <p>Tracen Replay is a noncommercial hobby project run by one person, its developer. It isn't affiliated with or endorsed by Cygames, Inc. The source code is public at <a :href="REPO_URL" target="_blank" rel="noopener noreferrer">{{ REPO_URL.replace("https://", "") }}</a>.</p>
    </section>

    <section class="guide-section">
      <div class="overline">Data</div>
      <h2>What Is Stored</h2>
      <ul class="guide-list">
        <li><b>Account.</b> Your email address, your display name, and a salted PBKDF2 hash of your password. The password itself is never stored.</li>
        <li><b>Recordings.</b> The video files you upload, with everything in them, including any sound or voice the recording picked up.</li>
        <li><b>Reports and Corrections.</b> The report from each analysis, a smaller copy of the recording for playback next to the report, the corrections you save in a review, and the log of each analysis.</li>
        <li><b>Session Cookie.</b> One cookie, tracen_session, keeps you signed in for up to 30 days. It's needed to sign in and isn't used for anything else. No other cookies are set.</li>
        <li><b>Browser Storage.</b> Your choice of light or dark theme, kept in your browser and never sent anywhere.</li>
        <li><b>IP Addresses.</b> Held in memory to limit sign-in attempts and new registrations. They aren't written to the database or to the service's logs. The hosting provider's network handles them as any web host does.</li>
        <li><b>Logs.</b> The service's logs include account IDs and file names, but not email addresses. They're kept for 30 days.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Limits</div>
      <h2>What Tracen Replay Doesn't Do</h2>
      <ul class="guide-list">
        <li><b>No Tracking.</b> There are no analytics, ads, third-party scripts or fonts. Every file a page loads comes from this website.</li>
        <li><b>No Selling or Sharing.</b> Your data isn't sold, rented or shared. It's only disclosed where the law requires it.</li>
        <li><b>No Other Use.</b> Recordings are only read to produce their reports. They aren't used to train models.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Storage</div>
      <h2>Where and How Long</h2>
      <ul class="guide-list">
        <li><b>Location.</b> Microsoft Azure data centers in the United States. Using the service from another country sends your data there.</li>
        <li><b>Original Recordings.</b> Deleted automatically 90 days after upload.</li>
        <li><b>Reports, Playback Copies and Corrections.</b> Kept until you delete the run or your account.</li>
        <li><b>Working Files.</b> The frames an analysis extracts are deleted when it ends. A paused analysis keeps them for 24 hours.</li>
        <li><b>Access.</b> Each account can only see its own recordings and reports. The operator can access stored data to run and repair the service.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Control</div>
      <h2>Access, Correction and Deletion</h2>
      <ul class="guide-list">
        <li><b>Download.</b> Every report can be downloaded from its page.</li>
        <li><b>Delete a Run.</b> Delete on the Runs page removes the recording, its playback copy and its reports.</li>
        <li><b>Delete the Account.</b> Delete Account at the bottom of this page removes your account and everything stored under it, all at once.</li>
        <li><b>After a Deletion.</b> In case of accidents, the operator can still recover deleted files for 14 days, and database backups are kept for 7 days. After that, the data is gone.</li>
        <li><b>Requests.</b> Anyone can ask what is stored about them, or ask for a correction or deletion, under laws such as the GDPR and the CCPA. <template v-if="CONTACT_EMAIL">Write to <a :href="`mailto:${CONTACT_EMAIL}`">{{ CONTACT_EMAIL }}</a>.</template><template v-else>Requests go through the project's <a :href="ISSUES_URL" target="_blank" rel="noopener noreferrer" @click="openExternal($event, ISSUES_URL)">issue tracker</a>. It's public, so don't include personal details there.</template></li>
        <li><b>Legal Basis.</b> Your data is processed to provide the service you signed up for, and to keep the service secure.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Other</div>
      <h2>Children, Security and Changes</h2>
      <ul class="guide-list">
        <li><b>Children.</b> The service isn't meant for anyone under 13, or under the age at which their country's law lets them consent to data processing (up to 16 in Europe). An account found to belong to someone younger is deleted.</li>
        <li><b>Security.</b> Connections use HTTPS, passwords are only stored as hashes, and the session cookie can't be read by scripts. No service is perfectly secure. If stored data is ever exposed, the affected accounts will be told by email.</li>
        <li><b>Changes.</b> Any change to this policy is posted on this page, with a new date, before it takes effect.</li>
      </ul>
    </section>
  </div>
  <section v-if="props.user" class="guide-section" style="margin-top: 26px">
    <div class="overline">Account</div>
    <div class="card" style="max-width: 560px">
      <h3 style="margin: 0 0 6px">Delete Account</h3>
      <p class="muted small" style="margin: 0 0 10px">Deletes {{ props.user.email }} and its recordings, reports, corrections and sessions. This can't be undone.</p>
      <button v-if="!asking" class="btn danger" @click="asking = true">Delete Account</button>
      <form v-else @submit.prevent="deleteAccount">
        <label class="field"><span>Password</span><input v-model="password" type="password" autocomplete="current-password" required /></label>
        <p v-if="error" class="error small">{{ error }}</p>
        <div class="row">
          <button class="btn danger" type="submit" :disabled="busy || !password">{{ busy ? "Deleting" : "Delete Everything" }}</button>
          <button class="btn quiet" type="button" :disabled="busy" @click="asking = false; password = ''; error = ''">Cancel</button>
        </div>
      </form>
    </div>
  </section>
</template>
