from pathlib import Path


UI = (Path(__file__).parents[1] / "ui" / "app.html").read_text(encoding="utf-8")


def test_ui_has_daily_workflow_controls() -> None:
    for control in ["search-form", "data-suffix=\"intro\"", "data-suffix=\"clean\"", "data-suffix=\"dirty\"", "data-suffix=\"trans\"", "data-suffix=\"QH\"", "data-suffix=\"SE\"", "data-suffix=\"segue\"", "data-suffix=\"short\"", "clear-modifier", "player-media"]:
        assert control in UI


def test_ui_has_sortable_bpm_column() -> None:
    assert 'className = "column-sort bpm-sort"' in UI
    assert 'className = "result-bpm"' in UI
    assert 'state.bpmSort === "asc" ? "desc" : "asc"' in UI
    assert "Number.isFinite(item.bpm)" in UI
    assert "item.display_name || item.name" in UI


def test_ui_has_sortable_size_column() -> None:
    assert 'className = "column-sort size-sort"' in UI
    assert 'state.sizeSort === "asc" ? "desc" : "asc"' in UI
    assert 'state.bpmSort ? "bpm" : "size_bytes"' in UI


def test_clear_modifier_removes_the_trailing_variant() -> None:
    assert "function clearQueryModifier(query)" in UI
    assert "runSearch(clearQueryModifier(queryInput.value || state.activeQuery), false)" in UI


def test_ui_strips_apostrophes_before_searching() -> None:
    assert "function stripApostrophes(query)" in UI
    assert "const clean = stripApostrophes(query).trim();" in UI


def test_modifier_searches_merge_exact_and_base_queries() -> None:
    assert "variantSuffixes.has(words[words.length - 1].toLowerCase())" in UI
    assert "Promise.all([fetcher(query), fetcher(baseQuery)])" in UI
    assert "window.mergeResultModels(JSON.stringify({ models, later_title_term: modifier }))" in UI


def test_ui_queries_and_labels_both_providers() -> None:
    assert "fetchDJPoolModel(query)" in UI
    assert "requestDJPoolSearch" in UI
    assert "djpoolSearchSucceeded" in UI
    assert "requestRVRemixSearch" in UI
    assert 'item.provider === "RVRemix"' in UI


def test_ui_queries_resolves_and_labels_crate_search() -> None:
    assert "requestCrateSearch" in UI
    assert "requestCrateResolve" in UI
    assert "crateSearchSucceeded" in UI
    assert "crateResolveSucceeded" in UI
    assert 'item.provider === "DJFolders"' in UI
    assert "state.crateEnabled" in UI
    assert "DJFolders" in UI
    assert "DJPoolRecords results ready; loading RVRemix" in UI
    assert "generation !== state.searchGeneration" in UI
    assert "rvremixSearchSucceeded" in UI
    assert "state.djpoolQueries.has(key)" in UI
    assert "state.rvremixQueries.has(key)" in UI
    assert "DJPoolRecords + RVRemix" in UI
    assert "requestRemoteDownload" in UI
    assert 'item.provider === "RVRemix"' in UI


def test_segue_modifier_is_immediately_after_trans() -> None:
    trans = UI.index('data-suffix="trans"')
    segue = UI.index('data-suffix="segue"')
    qh = UI.index('data-suffix="QH"')
    assert trans < segue < qh


def test_short_modifier_is_immediately_after_se() -> None:
    se = UI.index('data-suffix="SE"')
    short = UI.index('data-suffix="short"')
    clear = UI.index('id="clear-modifier"')
    assert se < short < clear


def test_ui_does_not_embed_remote_scripts_or_markup() -> None:
    assert "<script src=" not in UI
    assert "wp-content/plugins" not in UI
    assert "innerHTML" not in UI


def test_ui_supports_background_refresh_and_final_retry() -> None:
    assert "requestBootstrap" in UI
    assert "bootstrapSucceeded" in UI
    assert "finalAttempt" in UI


def test_preview_is_a_bottom_docked_pane() -> None:
    assert "bottom: 0" in UI
    assert "transform: translateY(100%)" in UI
    assert "--preview-height" in UI
    assert "preview-open" in UI


def test_preview_is_cached_before_native_playback() -> None:
    assert "requestPreview" in UI
    assert "Caching preview locally" in UI
    assert "previewReady" in UI
    assert 'document.createElement(kind === "video" ? "video" : "audio")' in UI
    assert "Playing cached preview." not in UI


def test_preview_uses_managed_download_instead_of_native_media_download() -> None:
    assert 'id="player-download"' in UI
    assert 'media.controlsList.add("nodownload")' in UI
    assert "requestCachedDownload" in UI
    assert "request_id: state.activePreviewRequest" in UI
    assert "playerDownload.hidden = !state.previewCanDownload" in UI
    assert "playerDownload.href = item.download_url" not in UI


def test_result_tile_is_the_preview_control() -> None:
    assert 'row.classList.add("previewable")' in UI
    assert 'preview.textContent = "▶"' in UI
    assert 'event.target.closest(".download")' in UI
    assert 'event.stopPropagation()' in UI
    assert ".result.previewable:hover .preview-action" in UI
    assert "min-height: 58px" in UI
    assert "padding: 7px 14px" in UI


def test_success_status_does_not_show_matches_message() -> None:
    assert "Showing matches for" not in UI
    assert "visibility: hidden;" in UI
    assert ".results { display: grid; gap: 8px; margin-top: 8px; }" in UI


def test_status_is_out_of_flow_to_prevent_layout_shift() -> None:
    assert ".status {" in UI
    assert "position: fixed;" in UI
    assert "bottom: calc(16px + var(--preview-height));" in UI
    assert "z-index: 70;" in UI


def test_download_completion_uses_temporary_status_toast() -> None:
    assert "downloadFinished(message, warning)" in UI
    assert "window.clearTimeout(window.djpoolDownloadStatusTimer)" in UI
    assert "}, 5000);" in UI


def test_sticky_search_panel_masks_scrolled_results() -> None:
    assert "background: var(--panel);" in UI
    assert ".search-shell::before" in UI
    assert "bottom: 100%;" in UI
    assert "height: 13px;" in UI
