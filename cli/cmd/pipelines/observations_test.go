package main

import (
	"bytes"
	"context"
	"encoding/json"
	"math"
	"strings"
	"testing"
	"testing/fstest"
)

func TestObservationArgumentsRejectBeforeDatasetLoad(t *testing.T) {
	for _, args := range [][]string{
		{"search", "--observations"},
		{"search", "--observations", ""},
		{"search", "--observations", "--image", "image.png"},
		{"search", "--observations", "--image", "image.png", ""},
		{"search", "--observations", "--limit", "0", "label"},
		{"observations"},
		{"observations", "--id", "", "sample:one"},
		{"observations", "--output", "text.json", "sample:one"},
		{"observations", "sample:one", "sample:two"},
	} {
		err := runWithFiles(context.Background(), args, &bytes.Buffer{}, fstest.MapFS{})
		if err == nil || strings.Contains(err.Error(), "development build") {
			t.Fatalf("invalid observation arguments reached dataset loading: %v (%v)", args, err)
		}
	}
}

func TestObservationUnavailableBeforeLoadingModelOrQueryImage(t *testing.T) {
	files := legacyBundle(t)
	for _, args := range [][]string{
		{"search", "--observations", "label"},
		{"search", "--observations", "--mode", "vector", "label"},
		{"search", "--observations", "--image", "does-not-exist.png", "label"},
		{"observations", "sample:one"},
		{"observations", "--id", strings.Repeat("a", 64), "sample:one"},
	} {
		err := runWithFiles(context.Background(), args, &bytes.Buffer{}, files)
		if err == nil || !strings.Contains(err.Error(), "no generated observations") {
			t.Fatalf("unexpected unavailable observations error: %v (%v)", args, err)
		}
	}
	var out bytes.Buffer
	if err := runWithFiles(context.Background(), []string{"info"}, &out, files); err != nil {
		t.Fatal(err)
	}
	var info map[string]json.RawMessage
	if err := json.Unmarshal(out.Bytes(), &info); err != nil || string(info["observations_available"]) != "false" || string(info["observations"]) != "null" {
		t.Fatal("legacy info reported observations", out.String(), err)
	}
}

func TestObservationProbeAgreementRejectsInvalidOrChangedVectors(t *testing.T) {
	expected := make([]float32, 384)
	expected[0] = 1
	if err := observationAgreement(expected, expected); err != nil {
		t.Fatal(err)
	}
	for _, edit := range []func([]float32){
		func(v []float32) { v[0] = -1 },
		func(v []float32) { v[0] = 0 },
		func(v []float32) { v[1] = .002 },
		func(v []float32) { v[1] = float32(math.NaN()) },
		func(v []float32) { v[1] = float32(math.Inf(1)) },
	} {
		actual := append([]float32(nil), expected...)
		edit(actual)
		if observationAgreement(actual, expected) == nil || observationAgreement(expected, actual) == nil {
			t.Fatal("incorrect observation embedding accepted")
		}
	}
	if observationAgreement(expected[:383], expected) == nil || observationAgreement(expected, expected[:383]) == nil {
		t.Fatal("truncated observation embedding accepted")
	}
}
