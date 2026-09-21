<script setup lang="ts">
import { hosted } from "../api";
import { openExternal } from "../mode";
import { CONTACT_EMAIL, EFFECTIVE, ISSUES_URL, LICENSE_URL, NOTICES_URL } from "../legal";

// The terms the website is offered under, and the notices that apply to the
// software everywhere: its license, the third-party components, and whose
// game it reads.
</script>

<template>
  <div class="page-head">
    <div>
      <h1>Terms of Use</h1>
      <p>The conditions for using Tracen Replay. In effect since {{ EFFECTIVE }}.</p>
    </div>
  </div>

  <div class="guide">
    <section class="guide-section">
      <div class="overline">Ownership</div>
      <h2>The Game and This Project</h2>
      <ul class="guide-list">
        <li><b>Unofficial.</b> Tracen Replay is a fan-made, noncommercial tool. It is not affiliated with, endorsed by or sponsored by Cygames, Inc.</li>
        <li><b>The Game.</b> Umamusume: Pretty Derby, its characters, names, images and footage belong to Cygames, Inc. and their respective owners. Game images on these pages show what the tool reads. Recording and sharing game footage stays subject to the game's own terms and the publisher's guidelines for fan content.</li>
        <li><b>The Software.</b> The source code is licensed under <a :href="LICENSE_URL" target="_blank" rel="noopener noreferrer" @click="openExternal($event, LICENSE_URL)">PolyForm Noncommercial 1.0.0</a>. Commercial use is not permitted. The third-party components it ships, with their licenses, are listed in the <a :href="NOTICES_URL" target="_blank" rel="noopener noreferrer" @click="openExternal($event, NOTICES_URL)">Third-Party Notices</a>.</li>
      </ul>
    </section>

    <section v-if="hosted" class="guide-section">
      <div class="overline">The Website</div>
      <h2>Accounts and Uploads</h2>
      <ul class="guide-list">
        <li><b>Agreement.</b> Creating an account or using the website means accepting these terms and the Privacy Policy. Anyone who does not accept them should not use the website.</li>
        <li><b>Age.</b> An account holder is at least 13, and old enough under the law of their country to agree to these terms.</li>
        <li><b>Uploads.</b> A person uploads only recordings they made themselves or have permission to upload. The uploader keeps every right they have in a recording, and gives the service permission to store and process it for the single purpose of producing and showing its report to that account.</li>
        <li><b>Not Allowed.</b> Unlawful content, content that is not a recording of the game, attempts to break, overload or get around the limits of the service, automated account creation, and any commercial use.</li>
        <li><b>Removal.</b> The operator may remove any upload or account, with or without notice, for breaking these terms or to protect the service.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">No Warranty</div>
      <h2>Provided As Is</h2>
      <ul class="guide-list">
        <li><b>No Guarantees.</b> Tracen Replay is free and provided as is, without warranty of any kind. Reports can contain mistakes. The website can change, lose data, limit use, or shut down at any time. Keep a copy of any recording that matters.</li>
        <li><b>Liability.</b> To the extent the law allows, the operator and contributors are not liable for any loss or damage arising from the use of the software or the website, or from the inability to use them.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Contact</div>
      <h2>Copyright Complaints and Questions</h2>
      <p class="muted">
        A rights holder who believes something on the website infringes their rights can ask for its removal, naming the content and their claim to it. It is removed promptly once the request is confirmed.
        <template v-if="CONTACT_EMAIL"> Write to <a :href="`mailto:${CONTACT_EMAIL}`">{{ CONTACT_EMAIL }}</a>.</template>
        <template v-else> Requests and questions go through the project's <a :href="ISSUES_URL" target="_blank" rel="noopener noreferrer" @click="openExternal($event, ISSUES_URL)">issue tracker</a>.</template>
      </p>
      <p class="muted">A change to these terms is published on this page with a new date before it takes effect. If a part of these terms cannot be enforced, the rest still applies.</p>
    </section>
  </div>
</template>
