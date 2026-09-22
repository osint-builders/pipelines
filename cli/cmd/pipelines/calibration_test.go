package main

import (
	"errors"
	"reflect"
	"testing"

	"github.com/osint-builders/pipelines/cli/internal/dataset"
)

type calibrationRecorder struct {
	enabled         bool
	seen            []dataset.CalibrationCandidate
	queryType, mode string
	observations    bool
	filter          dataset.Filter
	override        *dataset.CalibrationOverride
	err             error
}

func (r *calibrationRecorder) HasCalibration() bool { return r.enabled }
func (r *calibrationRecorder) Calibrate(kind, mode string, observations bool, filter dataset.Filter, candidates []dataset.CalibrationCandidate) (*dataset.CalibrationOverride, error) {
	r.seen, r.queryType, r.mode, r.observations, r.filter = candidates, kind, mode, observations, filter
	return r.override, r.err
}

func TestCalibrationClassifiesBeforeTrimmingAndPreservesRanking(t *testing.T) {
	cosine := .75
	results := []dataset.VisualResult{
		{Entity: dataset.Entity{ID: "test:one"}, Score: .75, Cosine: &cosine, Matches: []dataset.Match{{Channel: "image", Score: .875}}},
		{Entity: dataset.Entity{ID: "test:two"}, Score: .5},
	}
	expected := append([]dataset.VisualResult{}, results...)
	for _, limit := range []int{1, 2} {
		recorder := &calibrationRecorder{enabled: true, override: &dataset.CalibrationOverride{
			MatchStatus: "candidates", CalibrationStatus: "calibrated", Decision: dataset.CalibrationDecision{Reason: "accepted"},
		}}
		requested := calibrationLimit(recorder, limit)
		if requested != 2 {
			t.Fatal("runner-up not requested", requested)
		}
		response := map[string]any{"results": results, "match_status": "no_supported_match", "calibration_status": "uncalibrated"}
		filter := dataset.Filter{Source: "test"}
		if err := completeSearch(recorder, response, "image_text", "hybrid", true, filter, results, visualCalibrationCandidates(results), limit); err != nil {
			t.Fatal(err)
		}
		if len(recorder.seen) != 2 || recorder.seen[1].Score != .5 || recorder.queryType != "image_text" || recorder.mode != "hybrid" || !recorder.observations || recorder.filter.Source != "test" {
			t.Fatalf("wrong decision input: %+v", recorder)
		}
		if response["match_status"] != "candidates" || response["calibration_status"] != "calibrated" || response["decision"].(dataset.CalibrationDecision).Reason != "accepted" {
			t.Fatal(response)
		}
		if !reflect.DeepEqual(results, expected) || !reflect.DeepEqual(response["results"], expected[:limit]) {
			t.Fatal("calibration changed ranking, scores, provenance, or limit")
		}
	}
}

func TestCalibrationMissingProfilePreservesLegacyResponse(t *testing.T) {
	for _, enabled := range []bool{false, true} {
		recorder := &calibrationRecorder{enabled: enabled}
		results := []dataset.VisualResult{}
		response := map[string]any{"results": results, "match_status": "no_supported_match"}
		before := map[string]any{"results": results, "match_status": "no_supported_match"}
		if err := completeSearch(recorder, response, "text", "vector", false, dataset.Filter{}, results, nil, 1); err != nil {
			t.Fatal(err)
		}
		if !reflect.DeepEqual(response, before) {
			t.Fatal("legacy response changed", response)
		}
		if !enabled && calibrationLimit(recorder, 1) != 1 {
			t.Fatal("default bundle fetches extra candidates")
		}
	}
	recorder := &calibrationRecorder{enabled: true, err: errors.New("invalid scores")}
	if err := completeSearch(recorder, map[string]any{}, "text", "hybrid", false, dataset.Filter{}, []dataset.VisualResult{}, nil, 1); err == nil {
		t.Fatal("invalid calibration silently ignored")
	}
}
