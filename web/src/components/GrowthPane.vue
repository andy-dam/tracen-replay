<script setup lang="ts">
import type { TurnSummary } from "../api";
import StatChart from "./StatChart.vue";

// The opening value of every stat at every turn as one wide, clickable chart.
defineProps<{ turns: TurnSummary[]; selected: string }>();
const emit = defineEmits<{ select: [id: string] }>();
</script>

<template>
  <h3 class="pane-h" style="margin-top: 0">Stats Across the Run</h3>
  <p class="muted small" style="margin-bottom: 8px">Opening value at every turn. Click anywhere on the chart to open that turn; a hollow point is a turn whose opening was never on screen (a race day) and shows the value carried from the previous turn's entries; a gap is a turn with neither.</p>
  <StatChart :turns="turns" :selected="selected" @select="(id) => emit('select', id)" />
</template>
