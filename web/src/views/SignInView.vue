<script setup lang="ts">
import type { User } from "../api";
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
      <h1>{{ mode === "create" ? "Create Your Account" : "Welcome Back" }}</h1>
      <p class="lede">{{ mode === "create" ? "An account keeps your recordings and reports together. It lives on this machine only." : "Sign in to open your runs, queue an analysis or pick up a review." }}</p>
      <ul class="signin-points">
        <li><b>Record</b> a career at 1080p with the log panel open.</li>
        <li><b>Upload</b> it here; a full career takes about 45 minutes to analyze.</li>
        <li><b>Read</b> every turn beside the recording, and check what the report flags.</li>
      </ul>
      <p class="muted small">Runs on this computer. Nothing leaves it. <a href="#/guide">How It Works</a></p>
    </div>
    <SignInCard :initial-mode="mode" @signed-in="(u) => emit('signed-in', u)" />
  </div>
</template>
