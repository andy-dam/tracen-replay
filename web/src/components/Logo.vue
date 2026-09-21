<script setup lang="ts">
// The Tracen Replay mark: the green hat on a green rounded tile. The picture
// and every icon made from it come from desktop/icon/make_icons.py.
import logo from "../assets/logo.png";

withDefaults(defineProps<{ size?: number }>(), { size: 28 });
</script>

<template>
  <img :src="logo" :width="size" :height="size" alt="" aria-hidden="true" draggable="false" />
</template>
