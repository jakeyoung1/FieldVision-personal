# Errata: April 2026 RAG evaluation report

The [capstone evaluation PDF](https://github.com/jakeyoung1/FieldVision-personal/blob/c0d25b76d12561b617cc2d8c78b372e2315cf5c6/evaluation_report.pdf) (generated 2026-04-16) includes a Claude-generated report with two claims its own source data does not support. The PDF is kept as the historical record. These are the corrections.

The source and the report are reproduced verbatim in this folder (`source_transcription.txt`, `generated_report.txt`, extracted from the PDF with `pdftotext`). OCR spellings such as "Snittam" for Sniffen are kept as they appear.

| Report says | Source says | Correction |
|---|---|---|
| "Sniffen struck out swinging twice in this game (1-2 and 0-1 counts)" | 1st and 3rd innings: struck out swinging on 1-2. 5th inning: "grounded out to third base, 3b to 1b (0-1 K)" | Sniffen struck out twice, both on 1-2 counts. The 0-1 at-bat was a groundout. |
| "Griffith advanced multiple bases on wild pitches" | The wild pitches advanced Armstrong and scored Karimoto. Griffith was hit by a pitch, singled, stole second, and advanced on hits and a walk | Griffith did not advance on a wild pitch. |

The report's recommendation built on the first error ("aggressive early in counts", "chasing pitches") is not supported either. One of the two at-bats it cites is a ball put in play on 0-1.

## How these were caught, and how they are caught now

Both errors are found automatically by `backend/services/play_by_play.py`, which parses play-by-play into events and checks each outcome claim against them. `tests/test_play_by_play.py` runs it on these two files and requires exactly these two flags. `/api/analyze` runs the same check on every report built from play-by-play notes and shows unsupported claims above the grades.
