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
      <p>What Tracen Replay stores, where it is kept, and how it is deleted. In effect since {{ EFFECTIVE }}.</p>
    </div>
  </div>

  <div v-if="!hosted" class="guide">
    <section class="guide-section">
      <div class="overline">This Computer</div>
      <h2>{{ desktop ? "The Desktop Application" : "A Service Run From Source" }}</h2>
      <ul class="guide-list">
        <li><b>Storage.</b> Recordings, reports, corrections and settings are files in this computer's data folder. Nothing is uploaded, and the service accepts connections from this computer only.</li>
        <li><b>No Tracking.</b> No analytics, no advertising, no telemetry, no crash reports.</li>
        <li><b>Update Check.</b> A released build asks GitHub once a day for the newest release. The request carries the installed version and nothing else. GitHub sees the computer's internet address, as any website does. {{ desktop ? "Update Check in Settings turns it off." : "The -update-check=false flag turns it off." }}</li>
        <li><b>Deleting.</b> Deleting a run removes its recording and its reports. Removing the data folder removes everything.</li>
      </ul>
    </section>
    <section class="guide-section">
      <div class="overline">The Website</div>
      <h2>The Hosted Service</h2>
      <p class="muted">The website at the project's address stores uploads on a server. Its own Privacy Policy is on its Privacy page.</p>
    </section>
  </div>

  <div v-else class="guide">
    <section class="guide-section">
      <div class="overline">Operator</div>
      <h2>Who Runs the Service</h2>
      <p class="muted">Tracen Replay is a noncommercial hobby project run by one person, its developer. It is not affiliated with or endorsed by Cygames, Inc. The source code is public at <a :href="REPO_URL" target="_blank" rel="noopener noreferrer">{{ REPO_URL.replace("https://", "") }}</a>.</p>
    </section>

    <section class="guide-section">
      <div class="overline">Data</div>
      <h2>What Is Stored</h2>
      <ul class="guide-list">
        <li><b>Account.</b> The email address, the display name, and the password as a salted PBKDF2 hash. The password itself is never stored.</li>
        <li><b>Recordings.</b> The video files uploaded to an account, with everything in them, including any sound or voice the recording captured.</li>
        <li><b>Reports and Corrections.</b> The report each analysis produces, a smaller copy of the recording for playback beside the report, the corrections saved in a review, and the log of each analysis.</li>
        <li><b>Session Cookie.</b> One cookie, tracen_session, keeps an account signed in for up to 30 days. It is required for signing in and is not used for anything else. No other cookie is set.</li>
        <li><b>Browser Storage.</b> The light or dark theme choice, kept in the browser and never sent.</li>
        <li><b>Internet Addresses.</b> Held in memory to limit sign-in attempts and new registrations. They are not written to the database or to the service's logs. The hosting provider's network handles them as any web host does.</li>
        <li><b>Logs.</b> The service's logs name account identifiers and file names, not email addresses. They are kept for 30 days.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Limits</div>
      <h2>What Is Not Done</h2>
      <ul class="guide-list">
        <li><b>No Tracking.</b> No analytics, no advertising, no third-party scripts or fonts. Every file a page loads comes from the service itself.</li>
        <li><b>No Selling or Sharing.</b> Data is not sold, rented or shared. It is disclosed only where the law requires it.</li>
        <li><b>No Other Use.</b> Recordings are read to produce their reports and for nothing else. They are not used to train models.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Storage</div>
      <h2>Where and How Long</h2>
      <ul class="guide-list">
        <li><b>Location.</b> Microsoft Azure data centers in the United States. Using the service from another country sends the data there.</li>
        <li><b>Original Recordings.</b> Deleted automatically 90 days after the upload.</li>
        <li><b>Reports, Playback Copies and Corrections.</b> Kept until the run or the account is deleted.</li>
        <li><b>Working Files.</b> The frames an analysis extracts are deleted when it ends. A paused analysis keeps them for 24 hours.</li>
        <li><b>Access.</b> An account sees only its own recordings and reports. The operator can access stored data to run and repair the service.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Control</div>
      <h2>Access, Correction and Deletion</h2>
      <ul class="guide-list">
        <li><b>Download.</b> Every report can be downloaded from its page.</li>
        <li><b>Delete a Run.</b> Delete on the Runs page removes the recording, its playback copy and its reports.</li>
        <li><b>Delete the Account.</b> Delete Account at the bottom of this page removes the account and everything stored under it at once.</li>
        <li><b>After a Deletion.</b> Deleted files stay recoverable by the operator for 14 days and database backups for 7 days, as protection against accidents. After that they are gone.</li>
        <li><b>Requests.</b> Anyone may ask what is stored about them, or ask for a correction or deletion, under laws such as the GDPR and the CCPA. <template v-if="CONTACT_EMAIL">Write to <a :href="`mailto:${CONTACT_EMAIL}`">{{ CONTACT_EMAIL }}</a>.</template><template v-else>Requests go through the project's <a :href="ISSUES_URL" target="_blank" rel="noopener noreferrer" @click="openExternal($event, ISSUES_URL)">issue tracker</a>, which is public, so a request there should not contain personal details.</template></li>
        <li><b>Legal Basis.</b> Data is processed to provide the service an account asks for, and to keep the service secure.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Other</div>
      <h2>Children, Security and Changes</h2>
      <ul class="guide-list">
        <li><b>Children.</b> The service is not meant for anyone under 13, or under the age at which the law of their country lets them consent to data processing, which is up to 16 in Europe. An account found to belong to a younger person is deleted.</li>
        <li><b>Security.</b> Connections use HTTPS, passwords are stored only as hashes, and the session cookie cannot be read by scripts. No service is perfectly secure. If stored data is ever exposed, affected accounts are told by email.</li>
        <li><b>Changes.</b> A change to this policy is published on this page with a new date before it takes effect.</li>
      </ul>
    </section>
  </div>
  <section v-if="props.user" class="guide-section" style="margin-top: 26px">
    <div class="overline">Account</div>
    <div class="card" style="max-width: 560px">
      <h3 style="margin: 0 0 6px">Delete Account</h3>
      <p class="muted small" style="margin: 0 0 10px">Removes {{ props.user.email }}, its recordings, reports, corrections and sessions. This cannot be undone.</p>
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
