package main

import "github.com/osint-builders/pipelines/cli/internal/dataset"

type calibrationSource interface {
	HasCalibration() bool
	Calibrate(string, string, bool, dataset.Filter, []dataset.CalibrationCandidate) (*dataset.CalibrationOverride, error)
}

func calibrationLimit(d calibrationSource, limit int) int {
	if d.HasCalibration() && limit < 2 {
		return 2
	}
	return limit
}

func visualCalibrationCandidates(results []dataset.VisualResult) []dataset.CalibrationCandidate {
	rows := make([]dataset.CalibrationCandidate, len(results))
	for i, result := range results {
		rows[i] = dataset.CalibrationCandidate{Score: result.Score, Cosine: result.Cosine, Matches: result.Matches}
	}
	return rows
}

func completeSearch[T any](d calibrationSource, response map[string]any, queryType, textMode string, observations bool, filter dataset.Filter, results []T, candidates []dataset.CalibrationCandidate, limit int) error {
	override, err := d.Calibrate(queryType, textMode, observations, filter, candidates)
	if err != nil {
		return err
	}
	if override != nil {
		response["match_status"], response["calibration_status"], response["decision"] = override.MatchStatus, override.CalibrationStatus, override.Decision
	}
	if len(results) > limit {
		response["results"] = results[:limit]
	}
	return nil
}
