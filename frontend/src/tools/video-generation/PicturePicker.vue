<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue';
import { useI18n } from 'vue-i18n';

import type { VideoOptions } from './types';

const props = defineProps<{
  limits: Pick<VideoOptions, 'max_pictures' | 'max_picture_bytes' | 'picture_types'>;
}>();
const pictures = defineModel<File[]>({ required: true });

const { t } = useI18n();

const input = ref<HTMLInputElement | null>(null);
const error = ref('');

const accept = computed(() => props.limits.picture_types.join(','));
const full = computed(() => pictures.value.length >= props.limits.max_pictures);

// One object URL per picture for its thumbnail, released when the picture leaves the list.
const urls = new Map<File, string>();
const keys = new WeakMap<File, number>();
let nextKey = 0;

function urlOf(file: File): string {
  let url = urls.get(file);
  if (url === undefined) {
    url = URL.createObjectURL(file);
    urls.set(file, url);
  }
  return url;
}

function keyOf(file: File): number {
  let key = keys.get(file);
  if (key === undefined) {
    key = nextKey++;
    keys.set(file, key);
  }
  return key;
}

function release(file: File): void {
  const url = urls.get(file);
  if (url !== undefined) {
    URL.revokeObjectURL(url);
    urls.delete(file);
  }
}

onBeforeUnmount(() => urls.forEach((url) => URL.revokeObjectURL(url)));

function formatLimit(bytes: number): string {
  return `${Number((bytes / 1_000_000).toFixed(3))} MB`;
}

/** The catalog message for why a file cannot be added; `null` when it can. */
function problemWith(file: File): string | null {
  // An empty type (some browsers for HEIC) is left to the server, which checks the content.
  if (file.size === 0 || (file.type !== '' && !props.limits.picture_types.includes(file.type))) {
    return t('videoGeneration.pictures.unsupported');
  }
  if (file.size > props.limits.max_picture_bytes) {
    return t('videoGeneration.pictures.tooLarge', {
      limit: formatLimit(props.limits.max_picture_bytes),
    });
  }
  return null;
}

function onPick(event: Event): void {
  const target = event.target as HTMLInputElement;
  const added: File[] = [];
  let problem = '';
  for (const file of Array.from(target.files ?? [])) {
    const refused = problemWith(file);
    if (refused !== null) {
      problem ||= refused;
    } else if (pictures.value.length + added.length >= props.limits.max_pictures) {
      problem ||= t('videoGeneration.pictures.tooMany', { max: props.limits.max_pictures });
    } else {
      added.push(file);
    }
  }
  error.value = problem;
  if (added.length > 0) pictures.value = [...pictures.value, ...added];
  target.value = ''; // the same file can be picked again
}

/** The one reorder both the move buttons and drag and drop go through. */
function reorder(from: number, to: number): void {
  if (from === to) return;
  const next = [...pictures.value];
  const [file] = next.splice(from, 1);
  next.splice(to, 0, file!);
  pictures.value = next;
}

function move(index: number, offset: -1 | 1): void {
  reorder(index, index + offset);
}

// Drag and drop is an extra for pointer devices; the buttons stay the accessible way to reorder.
const dragFrom = ref<number | null>(null);
const dropOn = ref<number | null>(null);

function onDragStart(event: DragEvent, index: number): void {
  dragFrom.value = index;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', String(index)); // Firefox starts no drag without data
  }
}

function onDragOver(event: DragEvent, index: number): void {
  if (dragFrom.value === null) return; // not one of our pictures (e.g. a file from the desktop)
  event.preventDefault(); // allow the drop
  dropOn.value = index;
}

function onDrop(index: number): void {
  const from = dragFrom.value;
  onDragEnd();
  if (from !== null) reorder(from, index);
}

function onDragEnd(): void {
  dragFrom.value = null;
  dropOn.value = null;
}

function remove(index: number): void {
  const file = pictures.value[index]!;
  release(file);
  pictures.value = pictures.value.filter((_, i) => i !== index);
  error.value = '';
}
</script>

<template>
  <fieldset class="picker">
    <legend>{{ t('videoGeneration.pictures.label') }}</legend>
    <p class="hint">{{ t('videoGeneration.pictures.hint', { max: limits.max_pictures }) }}</p>
    <input
      ref="input"
      type="file"
      multiple
      hidden
      :accept="accept"
      data-test="picture-input"
      @change="onPick"
    />
    <button
      type="button"
      class="add"
      :disabled="full"
      data-test="add-pictures"
      @click="input?.click()"
    >
      {{ t('videoGeneration.pictures.add') }}
    </button>
    <p v-if="error" role="alert" class="error" data-test="picture-error">{{ error }}</p>
    <ol v-if="pictures.length > 0" class="list">
      <li
        v-for="(file, index) in pictures"
        :key="keyOf(file)"
        class="item"
        :class="{
          'drop-target': dropOn === index && dragFrom !== index,
          dragging: dragFrom === index,
        }"
        draggable="true"
        data-test="picture"
        @dragstart="onDragStart($event, index)"
        @dragover="onDragOver($event, index)"
        @drop.prevent="onDrop(index)"
        @dragend="onDragEnd"
      >
        <span class="position">{{ index + 1 }}</span>
        <img
          class="thumb"
          draggable="false"
          :src="urlOf(file)"
          :alt="t('videoGeneration.pictures.item', { position: index + 1 })"
        />
        <span class="controls">
          <button
            type="button"
            class="control"
            :disabled="index === 0"
            :aria-label="t('videoGeneration.pictures.moveUp', { position: index + 1 })"
            data-test="move-up"
            @click="move(index, -1)"
          >
            <span aria-hidden="true">&uarr;</span>
          </button>
          <button
            type="button"
            class="control"
            :disabled="index === pictures.length - 1"
            :aria-label="t('videoGeneration.pictures.moveDown', { position: index + 1 })"
            data-test="move-down"
            @click="move(index, 1)"
          >
            <span aria-hidden="true">&darr;</span>
          </button>
          <button
            type="button"
            class="control"
            :aria-label="t('videoGeneration.pictures.remove', { position: index + 1 })"
            data-test="remove"
            @click="remove(index)"
          >
            <span aria-hidden="true">&times;</span>
          </button>
        </span>
      </li>
    </ol>
  </fieldset>
</template>

<style scoped>
.picker {
  margin: 0;
  padding: 0;
  border: 0;
  min-width: 0;
}

.picker legend {
  padding: 0;
  font-weight: 600;
}

.hint {
  margin: 4px 0 8px;
  color: var(--muted);
}

.error {
  color: #b00020;
}

.list {
  display: grid;
  gap: 8px;
  margin: 12px 0 0;
  padding: 0;
  list-style: none;
}

.item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.item.drop-target {
  border-color: var(--fg);
}

.item.dragging {
  opacity: 0.5;
}

.position {
  flex: none;
  min-width: 1.5em;
  font-weight: 700;
  text-align: center;
}

.thumb {
  flex: none;
  width: 56px;
  height: 56px;
  border-radius: 6px;
  object-fit: cover;
  background: var(--border);
}

.controls {
  display: flex;
  flex: 1;
  justify-content: flex-end;
  gap: 4px;
}

.control {
  padding: 0;
  width: var(--touch);
  background: transparent;
  color: var(--fg);
  border: 1px solid var(--border);
  font-size: 1.25rem;
}

.control:disabled {
  opacity: 0.4;
  cursor: default;
}
</style>
