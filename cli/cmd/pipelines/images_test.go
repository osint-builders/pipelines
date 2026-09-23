package main

import (
	"archive/zip"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"math"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"testing/fstest"

	"github.com/osint-builders/pipelines/cli/internal/imagepreprocess"
)

func TestImageAndMediaArgumentsFailBeforeDatasetLoad(t *testing.T) {
	for _, args := range [][]string{
		{"search", "--image", ""},
		{"search", "--image", "image.png", "--mode", "hybrid"},
		{"search", "--mode", "vector", "--image", "image.png"},
		{"search", "--image", "image.png", ""},
		{"search", "--image", "image.png", strings.Repeat("雪", 1001)},
		{"media"},
		{"media", "--id", "", "sample:one"},
		{"media", "--output", "preview.jpg", "sample:one"},
		{"media", "--id", "sample:media:one", "--output", "", "sample:one"},
	} {
		err := runWithFiles(context.Background(), args, &bytes.Buffer{}, fstest.MapFS{})
		if err == nil || strings.Contains(err.Error(), "development build") {
			t.Fatalf("arguments reached dataset loading: %v (%v)", args, err)
		}
	}
}

func legacyBundle(t *testing.T) fstest.MapFS {
	t.Helper()
	index := []byte(`[{"id":"sample:one","source":"sample","kind":"radar","title":"Example"}]`)
	digest := sha256.Sum256(index)
	manifest, err := json.Marshal(map[string]any{"format_version": 2, "dataset_id": strings.Repeat("a", 64),
		"entities": 1, "model": map[string]any{"dimensions": 384}, "files": map[string]string{"index.json": hex.EncodeToString(digest[:])}})
	if err != nil {
		t.Fatal(err)
	}
	var body bytes.Buffer
	archive := zip.NewWriter(&body)
	for name, contents := range map[string][]byte{"index.json": index, "manifest.json": manifest} {
		entry, err := archive.Create(name)
		if err != nil {
			t.Fatal(err)
		}
		if _, err := entry.Write(contents); err != nil {
			t.Fatal(err)
		}
	}
	if err := archive.Close(); err != nil {
		t.Fatal(err)
	}
	return fstest.MapFS{"data/dataset.zip": {Data: body.Bytes()}}
}

func TestLegacyImageAvailabilityDoesNotRequireModels(t *testing.T) {
	files := legacyBundle(t)
	var out bytes.Buffer
	if err := runWithFiles(context.Background(), []string{"info"}, &out, files); err != nil {
		t.Fatal(err)
	}
	var info struct {
		Entities               int    `json:"entities"`
		ImageAvailable         bool   `json:"image_available"`
		ImageCalibrationStatus string `json:"image_calibration_status"`
	}
	if err := json.Unmarshal(out.Bytes(), &info); err != nil || info.Entities != 1 || info.ImageAvailable || info.ImageCalibrationStatus != "unavailable" {
		t.Fatalf("unexpected legacy info: %s (%v)", out.String(), err)
	}
	out.Reset()
	if err := runWithFiles(context.Background(), []string{"media", "sample:one"}, &out, files); err != nil || strings.TrimSpace(out.String()) != "[]" {
		t.Fatalf("legacy media: %s (%v)", out.String(), err)
	}
	for _, args := range [][]string{{"search", "--image", "image.png"}, {"search", "--image", "image.png", "radar"}} {
		err := runWithFiles(context.Background(), args, &bytes.Buffer{}, files)
		if err == nil || !strings.Contains(err.Error(), "no image search") {
			t.Fatalf("legacy image query: %v", err)
		}
	}
}

func TestImageReadLimitsAndExclusiveExport(t *testing.T) {
	directory := t.TempDir()
	filename := filepath.Join(directory, "image.png")
	if err := writeNewFile(filename, []byte("original")); err != nil {
		t.Fatal(err)
	}
	if err := writeNewFile(filename, []byte("replacement")); !os.IsExist(err) {
		t.Fatalf("existing export was not protected: %v", err)
	}
	body, err := readQueryImage(filename)
	if err != nil || string(body) != "original" {
		t.Fatalf("existing bytes changed: %q (%v)", body, err)
	}
	if _, err := readQueryImage(directory); err == nil {
		t.Fatal("image directory accepted")
	}
	file, err := os.Create(filepath.Join(directory, "oversized.png"))
	if err != nil {
		t.Fatal(err)
	}
	if err := file.Truncate(imagepreprocess.MaxImageBytes + 1); err != nil {
		t.Fatal(err)
	}
	if err := file.Close(); err != nil {
		t.Fatal(err)
	}
	if _, err := readQueryImage(file.Name()); err == nil {
		t.Fatal("oversized query accepted")
	}
}

func TestImageParityChecksBothDirectionAndComponents(t *testing.T) {
	expected := make([]float32, 512)
	expected[0] = 1
	if err := imageAgreement(expected, expected); err != nil {
		t.Fatal(err)
	}
	for _, edit := range []func([]float32){
		func(v []float32) { v[0] = -1 },
		func(v []float32) { v[0] = 0 },
		func(v []float32) { v[1] = float32(math.NaN()) },
		func(v []float32) { v[1] = float32(math.Inf(1)) },
		func(v []float32) { v[0], v[1] = float32(math.Sqrt(1-.02*.02)), .02 },
	} {
		actual := append([]float32(nil), expected...)
		edit(actual)
		if err := imageAgreement(actual, expected); err == nil {
			t.Fatalf("incorrect image embedding accepted: %v", actual[:2])
		}
		if err := imageAgreement(expected, actual); err == nil {
			t.Fatal("incorrect reference embedding accepted")
		}
	}
	if err := imageAgreement(expected[:511], expected); err == nil {
		t.Fatal("truncated embedding accepted")
	}
}
