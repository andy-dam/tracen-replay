<script setup lang="ts">
import { hosted, type User } from "../api";
import Logo from "../components/Logo.vue";
import SignInCard from "../components/SignInCard.vue";

// The sign-in screen: one card, one purpose. Reached from the front page,
// the top bar, or any page that needs an account while nobody is signed in.
defineProps<{ mode: "signin" | "create" }>();
const emit = defineEmits<{ "signed-in": [user: User] }>();
</script>

<template>
  <div class="signin-page">
    <div class="signin-copy">
      <a class="signin-mark" href="#/"><Logo :size="44" /></a>
      <h1>{{ mode === "create" ? "Create an Account" : "Welcome Back" }}</h1>
      <p class="lede">{{ mode === "create" ? (hosted ? "An account keeps your recordings and reports in one place." : "An account keeps your recordings and reports in one place. It exists only on this computer.") : "Sign in to see your runs, start an analysis or continue a review." }}</p>
      <ul class="signin-points">
        <li><b>Record</b> a career on PC at 1080p, or on a phone or tablet.</li>
        <li><b>Upload</b> the video here.</li>
        <li><b>Review</b> each turn next to the video, and check anything the report flags.</li>
      </ul>
      <p class="muted small">{{ hosted ? "Recordings are uploaded and analyzed on this website's servers." : "Everything runs on this computer. Nothing is uploaded." }} The <a href="#/guide">Guide</a> explains how to record a career.</p>
    </div>
    <SignInCard :initial-mode="mode" @signed-in="(u) => emit('signed-in', u)" />
  </div>
</template>
