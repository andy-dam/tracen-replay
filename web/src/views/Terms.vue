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
        <li><b>Unofficial.</b> Tracen Replay is a fan-made, noncommercial tool. It isn't affiliated with, endorsed by or sponsored by Cygames, Inc.</li>
        <li><b>The Game.</b> Umamusume: Pretty Derby and its characters, names, images and footage belong to Cygames, Inc. and their respective owners. The game images on these pages show what the tool reads. Recording and sharing game footage is still subject to the game's own terms and the publisher's guidelines for fan content.</li>
        <li><b>The Software.</b> The source code is licensed under <a :href="LICENSE_URL" target="_blank" rel="noopener noreferrer" @click="openExternal($event, LICENSE_URL)">PolyForm Noncommercial 1.0.0</a>, which doesn't allow commercial use. The third-party components it includes, and their licenses, are listed in the <a :href="NOTICES_URL" target="_blank" rel="noopener noreferrer" @click="openExternal($event, NOTICES_URL)">Third-Party Notices</a>.</li>
      </ul>
    </section>

    <section v-if="hosted" class="guide-section">
      <div class="overline">The Website</div>
      <h2>Accounts and Uploads</h2>
      <ul class="guide-list">
        <li><b>Agreement.</b> By creating an account or using the website, you accept these terms and the Privacy Policy. If you don't accept them, don't use the website.</li>
        <li><b>Age.</b> You must be at least 13, and old enough under your country's law to agree to these terms.</li>
        <li><b>Uploads.</b> Only upload recordings you made yourself or have permission to upload. You keep every right you have in a recording, and you give the service permission to store and process it for one purpose only: producing its report and showing it to your account.</li>
        <li><b>Not Allowed.</b> Unlawful content, anything that isn't a recording of the game, attempts to break, overload or get around the service's limits, automated account creation, and any commercial use.</li>
        <li><b>Removal.</b> The operator may remove any upload or account, with or without notice, for breaking these terms or to protect the service.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">No Warranty</div>
      <h2>Provided As Is</h2>
      <ul class="guide-list">
        <li><b>No Guarantees.</b> Tracen Replay is free and provided as is, without warranty of any kind. Reports can contain mistakes. The website can change, lose data, limit use, or shut down at any time, so keep your own copy of any recording that matters.</li>
        <li><b>Liability.</b> To the extent the law allows, the operator and contributors aren't liable for any loss or damage from using the software or the website, or from being unable to use them.</li>
      </ul>
    </section>

    <section class="guide-section">
      <div class="overline">Contact</div>
      <h2>Copyright Complaints and Questions</h2>
      <p>
        If you hold rights to something on the website and believe it infringes them, you can ask for it to be removed. Name the content and your claim to it. It's removed promptly once the request is confirmed.
        <template v-if="CONTACT_EMAIL"> Write to <a :href="`mailto:${CONTACT_EMAIL}`">{{ CONTACT_EMAIL }}</a>.</template>
        <template v-else> Requests and questions go through the project's <a :href="ISSUES_URL" target="_blank" rel="noopener noreferrer" @click="openExternal($event, ISSUES_URL)">issue tracker</a>.</template>
      </p>
      <p>Any change to these terms is posted on this page, with a new date, before it takes effect. If any part of these terms can't be enforced, the rest still applies.</p>
    </section>
  </div>
</template>
