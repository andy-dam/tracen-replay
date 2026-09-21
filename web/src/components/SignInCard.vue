<script setup lang="ts">
import { ref } from "vue";
import { api, ApiError, hosted, type User } from "../api";

const props = defineProps<{ initialMode?: "signin" | "create" }>();
const emit = defineEmits<{ "signed-in": [user: User] }>();
const mode = ref<"signin" | "create">(props.initialMode ?? "signin");
const email = ref("");
const password = ref("");
const name = ref("");
const error = ref("");
const busy = ref(false);

async function submit() {
  error.value = "";
  busy.value = true;
  try {
    const user = mode.value === "signin" ? await api.login(email.value, password.value) : await api.register(email.value, password.value, name.value);
    emit("signed-in", user);
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <div class="card signin">
    <div class="tabs">
      <button :class="{ active: mode === 'signin' }" @click="mode = 'signin'">Sign In</button>
      <button :class="{ active: mode === 'create' }" @click="mode = 'create'">Create an Account</button>
    </div>
    <form @submit.prevent="submit">
      <label v-if="mode === 'create'" class="field"><span>Display Name</span><input v-model="name" autocomplete="nickname" required maxlength="60" /></label>
      <label class="field"><span>Email</span><input v-model="email" type="email" autocomplete="email" required /></label>
      <label class="field"><span>Password</span><input v-model="password" type="password" :autocomplete="mode === 'create' ? 'new-password' : 'current-password'" required minlength="8" /></label>
      <p v-if="error" class="error small">{{ error }}</p>
      <button class="btn primary" type="submit" :disabled="busy">{{ mode === "signin" ? "Sign In" : "Create Account" }}</button>
      <p v-if="mode === 'create' && hosted" class="muted small" style="margin-top: 14px">Creating an account accepts the <a href="#/terms">Terms of Use</a> and the <a href="#/privacy">Privacy Policy</a>.</p>
      <p class="muted small" style="margin-top: 14px">
        {{ mode === "create" ? (hosted ? "Passwords need at least 8 characters." : "Passwords need at least 8 characters. Accounts live on this machine only.") : "New here? Create an account to upload a recording." }}
      </p>
    </form>
  </div>
</template>
