package main

import (
	"context"
	"errors"
	"fmt"
	"math"

	"github.com/osint-builders/pipelines/cli/internal/dataset"
	"github.com/osint-builders/pipelines/cli/internal/embedding"
)

func verifyObservations(ctx context.Context, d *dataset.Dataset) (int, error) {
	probes, err := d.ObservationProbes()
	if err != nil {
		return 0, err
	}
	encoder, err := embedding.New(ctx, d.Files)
	if err != nil {
		return 0, err
	}
	defer encoder.Close()
	for i, probe := range probes {
		actual, err := encoder.Encode(ctx, probe.Text)
		if err != nil {
			return 0, err
		}
		if err := observationAgreement(actual, probe.Vector); err != nil {
			return 0, fmt.Errorf("observation text probe %d failed: %w", i, err)
		}
	}
	return len(probes), nil
}

func observationAgreement(actual, expected []float32) error {
	if len(actual) != 384 || len(expected) != 384 {
		return errors.New("expected 384 text embedding components")
	}
	var deltaSum float64
	for i, value := range actual {
		delta := float64(value) - float64(expected[i])
		deltaSum += delta * delta
	}
	if math.IsNaN(deltaSum) || math.IsInf(deltaSum, 0) || math.Sqrt(deltaSum) > 0.001 {
		return errors.New("observation embedding parity mismatch")
	}
	return nil
}
