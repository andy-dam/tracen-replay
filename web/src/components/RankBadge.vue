<script setup lang="ts">
import { computed } from "vue";
import { statRank } from "../format";

const props = defineProps<{ value: number | null | undefined; small?: boolean }>();
const rank = computed(() => statRank(props.value));
</script>

<template>
  <span v-if="rank" class="rank" :class="['tier-' + rank.tier, { small }]" :title="`${rank.label} · ${value}`">
    <span class="rank-base">{{ rank.base }}</span>
    <sup v-if="rank.plus" class="rank-plus">+</sup>
    <sup v-else-if="rank.digit" class="rank-digit">{{ rank.digit }}</sup>
  </span>
  <span v-else class="rank tier-none" :class="{ small }">?</span>
</template>
