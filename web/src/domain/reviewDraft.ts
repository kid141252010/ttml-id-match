import type { PairPreview, SelectionPayload, SourceKey } from '@/api/types';

export interface ReviewDraft {
  selections: Record<string, SelectionPayload>;
}

export function createReviewDraft(previews: PairPreview[]): ReviewDraft {
  return {
    selections: Object.fromEntries(
      previews.map((preview) => [preview.pair_id, cloneSelection(preview.default_selection)]),
    ),
  };
}

export function selectionCount(draft: ReviewDraft): number {
  return Object.values(draft.selections).reduce(
    (total, selection) => total + Object.values(selection.sources).reduce((sum, ids) => sum + ids.length, 0),
    0,
  );
}

export function selectionsPayload(draft: ReviewDraft, previews: PairPreview[]): SelectionPayload[] {
  return previews.map((preview) =>
    cloneSelection(draft.selections[preview.pair_id] ?? { pair_id: preview.pair_id, sources: {} }),
  );
}

export function toggleDraftCandidate(
  draft: ReviewDraft,
  pairId: string,
  source: SourceKey,
  candidateId: string,
): ReviewDraft {
  const current = draft.selections[pairId] ?? { pair_id: pairId, sources: {} };
  const ids = new Set(current.sources[source] ?? []);
  if (ids.has(candidateId)) ids.delete(candidateId);
  else ids.add(candidateId);

  return {
    selections: {
      ...draft.selections,
      [pairId]: {
        pair_id: pairId,
        sources: { ...cloneSources(current.sources), [source]: [...ids] },
      },
    },
  };
}

function cloneSelection(selection: SelectionPayload): SelectionPayload {
  return { pair_id: selection.pair_id, sources: cloneSources(selection.sources) };
}

function cloneSources(sources: Record<SourceKey, string[]>): Record<SourceKey, string[]> {
  return Object.fromEntries(Object.entries(sources).map(([key, ids]) => [key, [...ids]]));
}
