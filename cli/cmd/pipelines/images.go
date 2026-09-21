package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"

	"github.com/osint-builders/pipelines/cli/internal/dataset"
	"github.com/osint-builders/pipelines/cli/internal/embedding"
	"github.com/osint-builders/pipelines/cli/internal/imageembedding"
	"github.com/osint-builders/pipelines/cli/internal/imagepreprocess"
)

func loadImageEncoder(ctx context.Context, d *dataset.Dataset) (*imageembedding.Encoder, error) {
	if _, err := d.ImageModel(); err != nil {
		return nil, err
	}
	return imageembedding.NewFromFS(ctx, d.Files, "image/model.json")
}

func searchImage(ctx context.Context, d *dataset.Dataset, filename, query string, filter dataset.Filter, limit int, observations bool, output *json.Encoder) error {
	if !d.HasImages() {
		return errors.New("this dataset has no image search model or gallery")
	}
	body, err := readQueryImage(filename)
	if err != nil {
		return err
	}
	if query != "" {
		if err := d.PrepareTextSearch(observations); err != nil {
			return err
		}
	}
	encoder, err := loadImageEncoder(ctx, d)
	if err != nil {
		return err
	}
	encoded, err := encoder.EncodeImage(ctx, body)
	_ = encoder.Close()
	if err != nil {
		return err
	}
	var textVector []float32
	queryType := "image"
	if query != "" {
		textEncoder, err := embedding.New(ctx, d.Files)
		if err != nil {
			return err
		}
		textVector, err = textEncoder.Encode(ctx, query)
		_ = textEncoder.Close()
		if err != nil {
			return err
		}
		queryType = "image_text"
	}
	var results []dataset.VisualResult
	if observations {
		results, err = d.SearchImagesWithObservations(encoded.Normalized, textVector, query, filter, limit)
	} else {
		results, err = d.SearchImages(encoded.Normalized, textVector, query, filter, limit)
	}
	if err != nil {
		return err
	}
	digest := sha256.Sum256(body)
	response := map[string]any{"dataset_id": d.Manifest.DatasetID, "query": query,
		"query_type": queryType, "mode": queryType, "query_image_sha256": hex.EncodeToString(digest[:]),
		"match_status": "no_supported_match", "calibration_status": "uncalibrated", "results": results}
	if observations {
		response["observations"] = true
	}
	if query != "" && d.Manifest.Search != nil {
		response["ranking_policy"], response["score_kind"] = d.Manifest.Search, "ranking_signal"
	}
	return output.Encode(response)
}

func readQueryImage(filename string) ([]byte, error) {
	file, err := os.Open(filename)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	info, err := file.Stat()
	if err != nil {
		return nil, err
	}
	if !info.Mode().IsRegular() || info.Size() < 1 || info.Size() > imagepreprocess.MaxImageBytes {
		return nil, errors.New("image query requires a nonempty regular file up to 20 MiB")
	}
	body, err := io.ReadAll(io.LimitReader(file, imagepreprocess.MaxImageBytes+1))
	if err != nil {
		return nil, err
	}
	if len(body) == 0 || len(body) > imagepreprocess.MaxImageBytes {
		return nil, errors.New("image query exceeds 20 MiB or is empty")
	}
	return body, nil
}

func verifyImages(ctx context.Context, d *dataset.Dataset) (int, error) {
	if !d.HasImages() {
		return 0, nil
	}
	probes, err := d.ImageProbes()
	if err != nil {
		return 0, err
	}
	if len(probes) < 3 {
		return 0, errors.New("image verification requires at least three probes")
	}
	encoder, err := loadImageEncoder(ctx, d)
	if err != nil {
		return 0, err
	}
	defer encoder.Close()
	for _, probe := range probes {
		body, err := d.Read(probe.ImageMember)
		if err != nil {
			return 0, err
		}
		actual, err := encoder.EncodeImage(ctx, body)
		if err != nil {
			return 0, err
		}
		if err := imageAgreement(actual.Normalized, probe.Vector); err != nil {
			return 0, fmt.Errorf("image embedding parity failed for %s: %w", probe.ID, err)
		}
	}
	return len(probes), nil
}

func imageAgreement(actual, expected []float32) error {
	if len(actual) != 512 || len(expected) != 512 {
		return errors.New("expected 512 image embedding components")
	}
	var dot, normActual, normExpected, maxAbs float64
	for i, value := range actual {
		a, b := float64(value), float64(expected[i])
		if math.IsNaN(a) || math.IsInf(a, 0) || math.IsNaN(b) || math.IsInf(b, 0) {
			return errors.New("nonfinite image embedding")
		}
		dot += a * b
		normActual += a * a
		normExpected += b * b
		maxAbs = math.Max(maxAbs, math.Abs(a-b))
	}
	normActual, normExpected = math.Sqrt(normActual), math.Sqrt(normExpected)
	if math.Abs(normActual-1) > 1e-5 || math.Abs(normExpected-1) > 1e-5 {
		return errors.New("image embedding is not normalized")
	}
	cosine := dot / (normActual * normExpected)
	if cosine < .999 || maxAbs > .01 {
		return fmt.Errorf("cosine %g, max absolute difference %g", cosine, maxAbs)
	}
	return nil
}

func writeNewFile(filename string, body []byte) error {
	file, err := os.OpenFile(filename, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	n, err := file.Write(body)
	if err == nil && n != len(body) {
		err = io.ErrShortWrite
	}
	if closeErr := file.Close(); err == nil {
		err = closeErr
	}
	if err != nil {
		_ = os.Remove(filename)
	}
	return err
}
