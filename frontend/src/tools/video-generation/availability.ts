import { getJson } from '@/api/client';

interface ListedTool {
  id: string;
}

/**
 * True only when the server lists its tools and Video Generation is not among them (the tool is not
 * configured). A list that cannot be read, or has an unexpected shape, does not hide the tool.
 */
export async function isToolHidden(toolId: string): Promise<boolean> {
  try {
    const listed = await getJson<ListedTool[]>('/api/tools');
    return Array.isArray(listed) && !listed.some((tool) => tool?.id === toolId);
  } catch {
    return false;
  }
}
