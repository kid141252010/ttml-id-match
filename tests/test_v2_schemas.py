from __future__ import annotations

import unittest

from pydantic import ValidationError

from server.v2.schemas import (
    ApplyFileResult,
    ApplyRequest,
    ApplyResponse,
    Candidate,
    ChangePlanRequest,
    ChangePlanResponse,
    ChangePlanSummary,
    ErrorResponse,
    Evidence,
    MetadataChangeSummary,
    NormalizationSummary,
    PairFile,
    PairFiles,
    PairingIssue,
    PairingPair,
    PairingPlanResponse,
    PairPreview,
    PreviewJob,
    Selection,
    SourceKey,
    SourceResult,
)


class V2SchemaTests(unittest.TestCase):
    def test_candidate_and_source_result_are_public_json_round_trip_models(self) -> None:
        candidate = Candidate(
            id="qq-1",
            source="qq_music",
            title=None,
            artists=["Artist"],
            album=None,
            aliases=["Alias"],
            identifiers={"mid": "mid-1"},
            group="mainland",
            rank=1,
            recommended=True,
            evidence=[
                Evidence(
                    field="title",
                    relation="exact",
                    expected="Song",
                    actual="Song",
                )
            ],
            duration_ms=123_000,
            release_date="2026-07-10",
        )
        result = SourceResult(
            source="qq_music",
            candidates=[candidate],
            groups={"mainland": ["qq-1"]},
            recommended_ids=["qq-1"],
            warnings=["catalog was incomplete"],
        )

        payload = result.model_dump(mode="json")
        self.assertIsNone(payload["candidates"][0]["title"])
        self.assertIsNone(payload["candidates"][0]["album"])
        self.assertNotIn("score", payload["candidates"][0])
        self.assertEqual(
            SourceResult.model_validate_json(result.model_dump_json()),
            result,
        )

        with self.assertRaises(ValidationError):
            Candidate(id="qq-1", source="qq_music", rank=1, score=99)

    def test_selection_uses_an_extensible_source_map(self) -> None:
        self.assertIs(SourceKey, str)
        selection = Selection(
            pair_id="pair-1",
            sources={
                "apple_music": ["apple-us-1", "apple-jp-1"],
                "ncm_music": ["ncm-1"],
            },
        )

        payload = selection.model_dump(mode="json")
        self.assertEqual(
            payload["sources"],
            {
                "apple_music": ["apple-us-1", "apple-jp-1"],
                "ncm_music": ["ncm-1"],
            },
        )
        self.assertNotIn("qq_music", payload)
        self.assertEqual(
            Selection.model_validate_json(selection.model_dump_json()),
            selection,
        )

        extension = Selection(pair_id="pair-1", sources={"bandcamp": ["track-1"]})
        self.assertEqual(extension.sources, {"bandcamp": ["track-1"]})

    def test_change_plan_summary_exposes_metadata_normalization_and_hashes(self) -> None:
        summary = ChangePlanSummary(
            input_sha256="input-hash",
            output_sha256="output-hash",
            final_text="<tt>final</tt>",
            changed=True,
            metadata=MetadataChangeSummary(
                added={"musicName": ["Song"]},
                replaced={"isrc": ["ISRC-1"]},
                skipped={"album": ["Existing Album"]},
                changed=True,
            ),
            normalization=NormalizationSummary(
                language_changed=True,
                body_text_changed=True,
                removed_translations=1,
                removed_transliterations=2,
                changed=True,
            ),
        )

        self.assertEqual(
            summary.model_dump(mode="json"),
            {
                "input_sha256": "input-hash",
                "output_sha256": "output-hash",
                "final_text": "<tt>final</tt>",
                "changed": True,
                "metadata": {
                    "added": {"musicName": ["Song"]},
                    "replaced": {"isrc": ["ISRC-1"]},
                    "skipped": {"album": ["Existing Album"]},
                    "changed": True,
                },
                "normalization": {
                    "language_changed": True,
                    "body_text_changed": True,
                    "removed_translations": 1,
                    "removed_transliterations": 2,
                    "changed": True,
                },
            },
        )
        self.assertEqual(
            ChangePlanSummary.model_validate_json(summary.model_dump_json()),
            summary,
        )

    def test_pair_preview_round_trips_dynamic_source_results_and_file_hashes(self) -> None:
        selection = Selection(
            pair_id="pair-1",
            sources={"spotify": ["spotify-us-1"]},
        )
        preview = PairPreview(
            pair_id="pair-1",
            files=PairFiles(
                ttml=PairFile(filename="Song.ttml", sha256="ttml-hash"),
                audio=PairFile(filename="Song.flac", sha256="audio-hash"),
            ),
            sources={
                "spotify": SourceResult(
                    source="spotify",
                    candidates=[
                        Candidate(
                            id="spotify-us-1",
                            source="spotify",
                            title="Song",
                            album="Album",
                            rank=1,
                            recommended=True,
                        )
                    ],
                    groups={"US": ["spotify-us-1"]},
                    recommended_ids=["spotify-us-1"],
                )
            },
            default_selection=selection,
            baseline_change_plan=ChangePlanSummary(
                input_sha256="ttml-hash",
                output_sha256="output-hash",
                final_text="<tt>final</tt>",
                changed=True,
                metadata=MetadataChangeSummary(
                    added={"spotifyId": ["spotify-us-1"]}
                ),
                normalization=NormalizationSummary(),
            ),
        )

        payload = preview.model_dump(mode="json")
        self.assertEqual(list(payload["sources"]), ["spotify"])
        self.assertEqual(payload["files"]["audio"]["filename"], "Song.flac")
        self.assertEqual(
            PairPreview.model_validate_json(preview.model_dump_json()),
            preview,
        )

    def test_pairing_plan_response_preserves_ambiguous_audio_issues(self) -> None:
        response = PairingPlanResponse(
            pairs=[
                PairingPair(
                    pair_id="pair-1",
                    status="ambiguous",
                    ttml_path="Song.ttml",
                    audio_path=None,
                    audio_candidates=["Song.mp3", "Song.wav"],
                )
            ],
            issues=[
                PairingIssue(
                    code="ambiguous_audio",
                    pair_id="pair-1",
                    ttml_path="Song.ttml",
                    audio_candidates=["Song.mp3", "Song.wav"],
                )
            ],
        )

        self.assertEqual(response.pairs[0].status, "ambiguous")
        self.assertEqual(response.issues[0].code, "ambiguous_audio")
        self.assertEqual(
            PairingPlanResponse.model_validate_json(response.model_dump_json()),
            response,
        )

        with self.assertRaises(ValidationError):
            PairingPair(
                pair_id="pair-1",
                status="guessed",
                ttml_path="Song.ttml",
            )

    def test_preview_job_accepts_all_v2_states_and_nullable_snapshot_id(self) -> None:
        statuses = (
            "pending",
            "running",
            "completed",
            "completed_with_errors",
            "failed",
        )
        for status in statuses:
            with self.subTest(status=status):
                job = PreviewJob(
                    job_id="job-1",
                    status=status,
                    total=2,
                    completed=1,
                    snapshot_id="snapshot-1" if status == "completed" else None,
                    errors=(
                        [
                            ErrorResponse(
                                code="source_unavailable",
                                message="QQ Music was unavailable",
                                retryable=True,
                                details={"source": "qq_music"},
                            )
                        ]
                        if status in {"completed_with_errors", "failed"}
                        else []
                    ),
                )

                self.assertEqual(
                    PreviewJob.model_validate_json(job.model_dump_json()),
                    job,
                )

        with self.assertRaises(ValidationError):
            PreviewJob(
                job_id="job-1",
                status="complete",
                total=1,
                completed=1,
            )

    def test_error_response_has_one_consistent_json_shape(self) -> None:
        error = ErrorResponse(
            code="snapshot_stale",
            message="The uploaded files changed after preview",
            retryable=False,
            details={"snapshot_id": "snapshot-1", "changed_pairs": ["pair-1"]},
        )

        self.assertEqual(
            error.model_dump(mode="json"),
            {
                "code": "snapshot_stale",
                "message": "The uploaded files changed after preview",
                "retryable": False,
                "details": {
                    "snapshot_id": "snapshot-1",
                    "changed_pairs": ["pair-1"],
                },
            },
        )
        self.assertEqual(
            ErrorResponse.model_validate_json(error.model_dump_json()),
            error,
        )

    def test_change_plan_request_and_response_round_trip_with_final_text(self) -> None:
        selection = Selection(
            pair_id="pair-1",
            sources={"qq_music": ["qq-1"]},
        )
        request = ChangePlanRequest(
            snapshot_id="snapshot-1",
            selection=selection,
        )
        response = ChangePlanResponse(
            snapshot_id="snapshot-1",
            pair_id="pair-1",
            input_sha256="input-hash",
            output_sha256="output-hash",
            changed=True,
            final_text="<tt/>",
            metadata=MetadataChangeSummary(added={"qqMusicId": ["qq-1"]}),
            normalization=NormalizationSummary(),
        )

        self.assertEqual(
            ChangePlanRequest.model_validate_json(request.model_dump_json()),
            request,
        )
        self.assertEqual(response.model_dump(mode="json")["final_text"], "<tt/>")
        self.assertEqual(
            ChangePlanResponse.model_validate_json(response.model_dump_json()),
            response,
        )

    def test_apply_request_and_response_are_snapshot_bound_round_trip_models(self) -> None:
        selection = Selection(
            pair_id="pair-1",
            sources={"apple_music": ["apple-1"]},
        )
        request = ApplyRequest(
            snapshot_id="snapshot-1",
            selections=[selection],
        )
        response = ApplyResponse(
            snapshot_id="snapshot-1",
            succeeded=1,
            failed=0,
            skipped=0,
            files=[
                ApplyFileResult(
                    pair_id="pair-1",
                    ttml="Song.ttml",
                    status="applied",
                    output_sha256="output-hash",
                    backup="Song.ttml.bak",
                )
            ],
        )

        self.assertEqual(
            ApplyRequest.model_validate_json(request.model_dump_json()),
            request,
        )
        self.assertEqual(
            ApplyResponse.model_validate_json(response.model_dump_json()),
            response,
        )


if __name__ == "__main__":
    unittest.main()
