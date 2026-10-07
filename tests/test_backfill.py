"""Descarga y lectura del histórico."""

from __future__ import annotations

import gzip
import json
from datetime import date
from itertools import pairwise
from pathlib import Path

from sportstar.backfill import load_backfill, month_ranges


class TestMonthRanges:
    def test_a_full_season_is_eight_requests(self) -> None:
        """La API acepta rangos, así que se pide mes a mes.

        Día a día serían 180 peticiones para una temporada.
        """
        ranges = month_ranges(date(2024, 3, 20), date(2024, 10, 1))
        assert len(ranges) == 8

    def test_respects_the_requested_boundaries(self) -> None:
        ranges = month_ranges(date(2024, 3, 20), date(2024, 10, 1))
        assert ranges[0][0] == date(2024, 3, 20)
        assert ranges[-1][1] == date(2024, 10, 1)

    def test_ranges_are_contiguous_and_do_not_overlap(self) -> None:
        # Un hueco pierde partidos; un solape los duplica.
        ranges = month_ranges(date(2024, 3, 20), date(2024, 10, 1))
        for (_, end), (next_start, _) in pairwise(ranges):
            assert (next_start - end).days == 1

    def test_a_single_day_is_one_range(self) -> None:
        assert len(month_ranges(date(2024, 5, 15), date(2024, 5, 15))) == 1

    def test_handles_a_range_inside_one_month(self) -> None:
        ranges = month_ranges(date(2024, 5, 10), date(2024, 5, 20))
        assert ranges == [(date(2024, 5, 10), date(2024, 5, 20))]


class TestLoadBackfill:
    def test_reads_compressed_payloads(self, tmp_path: Path) -> None:
        path = tmp_path / "schedule_2024-04.json.gz"
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump({"dates": []}, handle)
        assert load_backfill(tmp_path) == [{"dates": []}]

    def test_reads_plain_json_too(self, tmp_path: Path) -> None:
        """La vía sin instalar nada produce JSON plano.

        Descargar las URLs desde el navegador y subirlas por la web de GitHub no
        comprime nada. Exigir `.gz` cerraría ese camino por un detalle de formato.
        """
        (tmp_path / "schedule_2024-04.json").write_text('{"dates": []}', encoding="utf-8")
        assert load_backfill(tmp_path) == [{"dates": []}]

    def test_reads_both_formats_in_chronological_order(self, tmp_path: Path) -> None:
        (tmp_path / "schedule_2024-05.json").write_text('{"month": 5}', encoding="utf-8")
        with gzip.open(tmp_path / "schedule_2024-04.json.gz", "wt", encoding="utf-8") as handle:
            json.dump({"month": 4}, handle)
        assert [p["month"] for p in load_backfill(tmp_path)] == [4, 5]

    def test_missing_directory_is_empty_not_an_error(self, tmp_path: Path) -> None:
        # Antes del primer backfill no hay directorio, y eso no es un fallo.
        assert load_backfill(tmp_path / "no-existe") == []

    def test_ignores_unrelated_files(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_text("nada", encoding="utf-8")
        (tmp_path / "schedule_2024-04.json").write_text('{"dates": []}', encoding="utf-8")
        assert len(load_backfill(tmp_path)) == 1
