package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"io/fs"
	"math"
	"os"
	"strings"

	"github.com/osint-builders/pipelines/cli/internal/assets"
	"github.com/osint-builders/pipelines/cli/internal/dataset"
	"github.com/osint-builders/pipelines/cli/internal/embedding"
)

var version = "development"

const help = `pipelines - offline equipment and entity search

Usage:
  pipelines search [--mode hybrid|vector] [--limit 10] [filters] "query"
  pipelines search --image PATH [--limit 10] [filters] ["query"]
  pipelines search --observations [--image PATH] [--limit 10] [filters] "query"
  pipelines similar [--limit 10] [filters] SOURCE:ID
  pipelines get [--format json|markdown|html|source] [--evidence PAGE_ID] SOURCE:ID
  pipelines media [--id MEDIA_ID] [--output PATH] SOURCE:ID
  pipelines observations [--id OBSERVATION_ID] SOURCE:ID
  pipelines info
  pipelines verify
  pipelines version
  pipelines notices

Filters: --source SOURCE --kind KIND --category CATEGORY
Place flags before the query or ID. Every search returns stable, source-qualified IDs.
JSON is the default output. Source export preserves the archived response bytes.
Image queries accept local JPEG/PNG files up to 20 MiB and 40 million pixels.
Image suggestions are uncalibrated. Media export writes the embedded preview.
Generated OCR/descriptions are searched only with --observations and remain uncalibrated.
All commands work offline. This executable never scrapes or downloads models.
`

func main() {
	if err := run(context.Background(), os.Args[1:], os.Stdout); err != nil {
		_ = json.NewEncoder(os.Stderr).Encode(map[string]string{"error": err.Error()})
		os.Exit(1)
	}
}

func run(ctx context.Context, args []string, out io.Writer) error {
	return runWithFiles(ctx, args, out, assets.Files)
}

func runWithFiles(ctx context.Context, args []string, out io.Writer, files fs.FS) error {
	if len(args) == 0 || args[0] == "--help" || args[0] == "help" || args[0] == "-h" {
		_, err := io.WriteString(out, help)
		return err
	}
	if args[0] == "version" {
		return json.NewEncoder(out).Encode(map[string]string{"version": version})
	}
	if args[0] == "notices" {
		for _, name := range []string{"data/MODEL-LICENSE.txt", "data/THIRD-PARTY-NOTICES.txt"} {
			body, err := fs.ReadFile(files, name)
			if err != nil {
				return err
			}
			if _, err = out.Write(body); err != nil {
				return err
			}
		}
		return nil
	}
	command := args[0]
	if command != "search" && command != "similar" && command != "get" && command != "media" && command != "observations" && command != "info" && command != "verify" {
		return fmt.Errorf("unknown command: %s", command)
	}
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	limit := 10
	mode, format, evidence := "hybrid", "json", ""
	imagePath, mediaID, outputPath := "", "", ""
	observationID := ""
	useObservations := false
	filter := dataset.Filter{}
	if command == "search" || command == "similar" {
		flags.IntVar(&limit, "limit", 10, "maximum results")
		flags.StringVar(&filter.Source, "source", "", "source filter")
		flags.StringVar(&filter.Kind, "kind", "", "kind filter")
		flags.StringVar(&filter.Category, "category", "", "category filter")
	}
	if command == "search" {
		flags.StringVar(&mode, "mode", "hybrid", "search mode")
		flags.StringVar(&imagePath, "image", "", "local JPEG or PNG query")
		flags.BoolVar(&useObservations, "observations", false, "include generated OCR and descriptions")
	}
	if command == "get" {
		flags.StringVar(&format, "format", "json", "output format")
		flags.StringVar(&evidence, "evidence", "", "select an evidence page")
	}
	if command == "media" {
		flags.StringVar(&mediaID, "id", "", "select a media record")
		flags.StringVar(&outputPath, "output", "", "write its embedded preview to a new file")
	}
	if command == "observations" {
		flags.StringVar(&observationID, "id", "", "select a generated observation")
	}
	if err := flags.Parse(args[1:]); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			_, err = io.WriteString(out, help)
			return err
		}
		return err
	}
	explicit := map[string]bool{}
	flags.Visit(func(f *flag.Flag) { explicit[f.Name] = true })
	imageQuery := explicit["image"]
	if imageQuery && (strings.TrimSpace(imagePath) == "" || explicit["mode"]) {
		return errors.New("--image requires a local file and cannot be combined with --mode")
	}
	if explicit["id"] && ((command == "media" && strings.TrimSpace(mediaID) == "") || (command == "observations" && strings.TrimSpace(observationID) == "")) || explicit["output"] && (strings.TrimSpace(outputPath) == "" || mediaID == "") {
		return errors.New("--id must not be empty; --output requires --id and a new file path")
	}
	needsArgument := command == "search" || command == "similar" || command == "get" || command == "media" || command == "observations"
	if useObservations && imageQuery && flags.NArg() == 0 {
		return errors.New("--observations requires a text query")
	}
	if imageQuery && flags.NArg() > 1 || !imageQuery && (needsArgument && flags.NArg() != 1 || !needsArgument && flags.NArg() != 0) {
		return errors.New("incorrect arguments; quote the query and place flags before it (see --help)")
	}
	if mode != "hybrid" && mode != "vector" {
		return errors.New("mode must be hybrid or vector")
	}
	if format != "json" && format != "markdown" && format != "html" && format != "source" {
		return errors.New("format must be json, markdown, html, or source")
	}
	if limit < 1 || limit > 100 {
		return errors.New("limit must be between 1 and 100")
	}
	value := flags.Arg(0)
	if needsArgument && (!imageQuery || flags.NArg() == 1) && strings.TrimSpace(value) == "" {
		return errors.New("query or ID must not be empty")
	}
	if command == "search" && len([]rune(value)) > 1000 {
		return errors.New("query exceeds 1000 characters")
	}
	data, err := fs.ReadFile(files, "data/dataset.zip")
	if err != nil {
		return errors.New("this development build has no dataset; build a bundle or download a release binary")
	}
	d, err := dataset.Open(data)
	if err != nil {
		return err
	}
	if useObservations && !d.HasObservations() {
		return errors.New("this dataset has no generated observations")
	}
	output := json.NewEncoder(out)
	output.SetIndent("", "  ")
	switch command {
	case "info":
		var imageModel json.RawMessage
		calibration := "unavailable"
		if d.HasImages() {
			imageModel, err = d.ImageModel()
			if err != nil {
				return err
			}
			calibration = "uncalibrated"
		}
		return output.Encode(map[string]any{"version": version, "dataset_id": d.Manifest.DatasetID, "content_sha256": d.Manifest.ContentSHA256, "entities": d.Manifest.Entities, "evidence_pages": d.Manifest.EvidencePages, "chunks": d.Manifest.Chunks, "model": d.Manifest.Model, "sources": d.Manifest.Sources, "image_available": d.HasImages(), "image": d.Manifest.Image, "image_model": imageModel, "image_calibration_status": calibration, "observations_available": d.HasObservations(), "observations": d.Manifest.Observations})
	case "get":
		raw, err := d.Export(value, format, evidence)
		if err != nil {
			return err
		}
		_, err = out.Write(raw)
		return err
	case "media":
		if outputPath != "" {
			body, err := d.MediaPreview(value, mediaID)
			if err != nil {
				return err
			}
			if err := writeNewFile(outputPath, body); err != nil {
				return err
			}
			digest := sha256.Sum256(body)
			return output.Encode(map[string]any{"dataset_id": d.Manifest.DatasetID, "entity_id": value, "media_id": mediaID, "bytes": len(body), "sha256": hex.EncodeToString(digest[:])})
		}
		records, err := d.Media(value, mediaID)
		if err != nil {
			return err
		}
		return output.Encode(records)
	case "observations":
		records, err := d.Observations(value, observationID)
		if err != nil {
			return err
		}
		output.SetEscapeHTML(false)
		return output.Encode(map[string]any{"dataset_id": d.Manifest.DatasetID, "entity_id": value, "observations": records.Observations, "recipes": records.Recipes})
	case "similar":
		vector, err := d.EntityVector(value)
		if err != nil {
			return err
		}
		results, err := d.Search(vector, "", false, filter, limit, value)
		if err != nil {
			return err
		}
		return output.Encode(map[string]any{"dataset_id": d.Manifest.DatasetID, "mode": "vector", "similar_to": value, "results": results})
	case "verify":
		if err = d.Verify(); err != nil {
			return err
		}
	}
	if imageQuery {
		return searchImage(ctx, d, imagePath, value, filter, limit, useObservations, output)
	}
	if command == "verify" {
		probes, err := verifyText(ctx, d)
		if err != nil {
			return err
		}
		imageProbes, err := verifyImages(ctx, d)
		if err != nil {
			return err
		}
		report := map[string]any{"ok": true, "dataset_id": d.Manifest.DatasetID, "entities": d.Manifest.Entities, "evidence_pages": d.Manifest.EvidencePages, "probes": probes, "image_probes": imageProbes}
		if d.HasObservations() {
			count, err := verifyObservations(ctx, d)
			if err != nil {
				return err
			}
			report["observation_probes"] = count
		}
		return output.Encode(report)
	}
	encoder, err := embedding.New(ctx, d.Files)
	if err != nil {
		return err
	}
	defer encoder.Close()
	vector, err := encoder.Encode(ctx, value)
	if err != nil {
		return err
	}
	if useObservations {
		results, err := d.SearchObservations(vector, value, mode == "hybrid", filter, limit)
		if err != nil {
			return err
		}
		return output.Encode(map[string]any{"dataset_id": d.Manifest.DatasetID, "query": value, "query_type": "text", "mode": mode, "observations": true, "match_status": "no_supported_match", "calibration_status": "uncalibrated", "results": results})
	}
	results, err := d.Search(vector, value, mode == "hybrid", filter, limit, "")
	if err != nil {
		return err
	}
	type textResult struct {
		dataset.Result
		Matches []dataset.Match `json:"matches"`
	}
	contributions := make([]textResult, len(results))
	for i, result := range results {
		match, err := d.TextMatch(result)
		if err != nil {
			return err
		}
		contributions[i] = textResult{result, []dataset.Match{match}}
	}
	status := "candidates"
	if len(results) == 0 {
		status = "no_supported_match"
	}
	return output.Encode(map[string]any{"dataset_id": d.Manifest.DatasetID, "query": value, "query_type": "text", "match_status": status, "mode": mode, "results": contributions})
}

func verifyText(ctx context.Context, d *dataset.Dataset) (int, error) {
	encoder, err := embedding.New(ctx, d.Files)
	if err != nil {
		return 0, err
	}
	defer encoder.Close()
	// Producer and consumer must use the same embedding space, including tokenization.
	raw, err := d.Read("probes.json")
	if err != nil {
		return 0, err
	}
	var probes []string
	if err = json.Unmarshal(raw, &probes); err != nil {
		return 0, err
	}
	raw, err = d.Read("probes.f32")
	if err != nil {
		return 0, err
	}
	reference, err := dataset.DecodeVectors(raw, len(probes), d.Manifest.Model.Dimensions)
	if err != nil {
		return 0, err
	}
	for i, probe := range probes {
		actual, err := encoder.Encode(ctx, probe)
		if err != nil {
			return 0, err
		}
		var errorSum float64
		for j, v := range actual {
			delta := float64(v - reference[i][j])
			errorSum += delta * delta
		}
		if math.IsNaN(errorSum) || math.Sqrt(errorSum) > 0.001 {
			return 0, fmt.Errorf("embedding parity failed for probe %d: L2 error %g", i, math.Sqrt(errorSum))
		}
	}
	return len(probes), nil
}
