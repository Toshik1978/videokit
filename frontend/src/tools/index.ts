import type { InjectionKey } from 'vue';
import type { RouteRecordRaw } from 'vue-router';

/** A frontend tool plugin; `id` equals the backend tool id (e.g. `youtube_prep`). */
export interface ToolModule {
  id: string;
  routes: RouteRecordRaw[];
  /** Catalog keys for the home card; the API's name/description are used when absent. */
  labels?: { name: string; description: string };
}

export function collectTools(modules: Record<string, ToolModule>): ToolModule[] {
  const seen = new Set<string>();
  return Object.values(modules).map((tool) => {
    if (seen.has(tool.id)) {
      throw new Error(`Duplicate tool id: ${tool.id}`);
    }
    seen.add(tool.id);
    return tool;
  });
}

export const tools: ToolModule[] = collectTools(
  import.meta.glob<ToolModule>('./*/index.ts', { eager: true }),
);

/** Injection key letting the home page receive the tool module list (tests override it). */
export const toolsKey: InjectionKey<ToolModule[]> = Symbol('videokit-tools');
