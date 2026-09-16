<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { api, ApiError, type User } from "./api";
import Home from "./views/Home.vue";
import Runs from "./views/Runs.vue";
import ReportView from "./views/ReportView.vue";
import ReviewView from "./views/ReviewView.vue";
import JobView from "./views/JobView.vue";
import SignInView from "./views/SignInView.vue";
import CheckQueue from "./views/CheckQueue.vue";
import Guide from "./views/Guide.vue";
import Logo from "./components/Logo.vue";

// Hash routing keeps the client dependency-free: #/, #/reports/<id>/<turn>, #/reports/<id>/<turn>/review, #/jobs/<id>.
const hash = ref(window.location.hash);
const onHash = () => (hash.value = window.location.hash);
onMounted(() => window.addEventListener("hashchange", onHash));
onUnmounted(() => window.removeEventListener("hashchange", onHash));

const route = computed(() => {
  const parts = hash.value.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "reports" && parts[1] && parts[2] && parts[3] === "review") return { name: "review", id: decodeURIComponent(parts[1]), turn: decodeURIComponent(parts[2]) };
  if (parts[0] === "reports" && parts[1]) return { name: "report", id: decodeURIComponent(parts[1]), turn: parts[2] ? decodeURIComponent(parts[2]) : "" };
  if (parts[0] === "jobs" && parts[1]) return { name: "job", id: decodeURIComponent(parts[1]), turn: "" };
  if (parts[0] === "runs") return { name: "runs", id: "", turn: "" };
  if (parts[0] === "check") return { name: "check", id: "", turn: "" };
  if (parts[0] === "guide") return { name: "guide", id: "", turn: "" };
  if (parts[0] === "signin") return { name: "signin", id: "", turn: "" };
  if (parts[0] === "signup") return { name: "signup", id: "", turn: "" };
  return { name: "home", id: "", turn: "" };
});

const user = ref<User | null>(null);
const checked = ref(false);
const notice = ref("");

async function loadUser() {
  try {
    user.value = await api.me();
  } catch (e) {
    user.value = null;
    if (e instanceof ApiError && e.status !== 401) notice.value = e.message;
  } finally {
    checked.value = true;
  }
}
onMounted(loadUser);

function signedIn(u: User) {
  user.value = u;
  window.location.hash = "#/runs";
}

async function signOut() {
  try {
    await api.logout();
  } finally {
    user.value = null;
    window.location.hash = "#/";
  }
}

// Theme: the system setting unless the viewer chose one; the choice is kept per browser.
const explicit = ref<"light" | "dark" | null>(null);
try {
  const stored = localStorage.getItem("tracen-theme");
  if (stored === "light" || stored === "dark") explicit.value = stored;
} catch {
  // storage unavailable
}
const systemDark = ref(window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false);
const media = window.matchMedia?.("(prefers-color-scheme: dark)");
const onMedia = (e: MediaQueryListEvent) => (systemDark.value = e.matches);
onMounted(() => media?.addEventListener("change", onMedia));
onUnmounted(() => media?.removeEventListener("change", onMedia));
const dark = computed(() => (explicit.value ? explicit.value === "dark" : systemDark.value));
function applyTheme() {
  if (explicit.value) document.documentElement.dataset.theme = explicit.value;
  else delete document.documentElement.dataset.theme;
}
applyTheme();
function toggleTheme() {
  explicit.value = dark.value ? "light" : "dark";
  try {
    localStorage.setItem("tracen-theme", explicit.value);
  } catch {
    // storage unavailable
  }
  applyTheme();
}

const initial = computed(() => (user.value?.display_name?.trim().charAt(0) || "?").toUpperCase());
</script>

<template>
  <header class="topbar">
    <div class="topbar-inner">
      <a class="wordmark" href="#/"><Logo :size="28" />Tracen Replay</a>
      <nav>
        <a href="#/" :class="{ active: route.name === 'home' }">Home</a>
        <a v-if="user" href="#/runs" :class="{ active: route.name === 'runs' }">Runs</a>
        <a v-if="user" href="#/check" :class="{ active: route.name === 'check' }">To check</a>
        <a href="#/guide" :class="{ active: route.name === 'guide' }">Guide</a>
      </nav>
      <span class="spacer"></span>
      <a v-if="checked && !user && route.name !== 'signin'" class="btn small primary" href="#/signin">Sign in</a>
      <button class="icon-btn" :title="dark ? 'Switch to the light theme' : 'Switch to the dark theme'" @click="toggleTheme">{{ dark ? "☀" : "☾" }}</button>
      <div v-if="user" class="userchip">
        <span class="avatar">{{ initial }}</span>
        <span><span class="who">Signed in as </span><strong>{{ user.display_name }}</strong></span>
        <button class="btn quiet small" @click="signOut">Sign out</button>
      </div>
    </div>
  </header>
  <div class="shell">
    <p v-if="notice" class="error">{{ notice }}</p>
    <main v-if="checked" class="sheet" :key="route.name + route.id">
      <Home v-if="route.name === 'home'" :user="user" />
      <Guide v-else-if="route.name === 'guide'" />
      <SignInView v-else-if="!user || route.name === 'signin' || route.name === 'signup'" :mode="route.name === 'signup' ? 'create' : 'signin'" @signed-in="signedIn" />
      <Runs v-else-if="route.name === 'runs'" />
      <CheckQueue v-else-if="route.name === 'check'" />
      <ReportView v-else-if="route.name === 'report'" :report-id="route.id" :turn-id="route.turn" />
      <ReviewView v-else-if="route.name === 'review'" :report-id="route.id" :turn-id="route.turn" />
      <JobView v-else-if="route.name === 'job'" :job-id="route.id" />
    </main>
  </div>
</template>
