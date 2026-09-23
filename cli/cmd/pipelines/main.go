package main

import (
	"bytes"
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
	"sort"
	"strings"

	"github.com/osint-builders/pipelines/cli/internal/assets"
	"github.com/osint-builders/pipelines/cli/internal/dataset"
	"github.com/osint-builders/pipelines/cli/internal/embedding"
)

var version = "development"

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
		command := ""
		if len(args) == 2 && args[0] == "help" {
			command = args[1]
		}
		return commandHelp(command, out)
	}
	if args[0] == "version" || args[0] == "--version" {
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
	if command != "search" && command != "similar" && command != "get" && command != "media" && command != "observations" && command != "info" && command != "verify" && !isResearchCommand(command) {
		return fmt.Errorf("unknown command: %s", command)
	}
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	limit := 10
	mode, format, evidence := "hybrid", "json", ""
	imagePath, mediaID, outputPath := "", "", ""
	observationID := ""
	relationType := ""
	useObservations := false
	options := searchOptions{Page: dataset.Page{Number: 1, Size: 10}}
	filter := dataset.Filter{}
	if command == "search" || command == "similar" || command == "list" {
		flags.IntVar(&limit, "limit", 10, "maximum results")
		flags.StringVar(&filter.Source, "source", "", "source filter")
		flags.StringVar(&filter.Kind, "kind", "", "kind filter")
		flags.StringVar(&filter.Category, "category", "", "category filter")
		flags.Func("where", "require a structured field predicate (repeatable)", func(value string) error {
			if strings.TrimSpace(value) == "" {
				return errors.New("--where must not be empty")
			}
			filter.Where = append(filter.Where, value)
			return nil
		})
	}
	if command == "search" {
		flags.StringVar(&mode, "mode", "hybrid", "search mode")
		flags.IntVar(&options.Page.Number, "page", 1, "result page")
		flags.BoolVar(&options.Raw, "raw", false, "include complete saved records")
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
	if command == "relationships" {
		flags.StringVar(&relationType, "type", "", "relationship type")
	}
	if err := parseFlags(flags, args[1:]); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return commandHelp(command, out)
		}
		return err
	}
	explicit := map[string]bool{}
	flags.Visit(func(f *flag.Flag) { explicit[f.Name] = true })
	if err := validateResearchArguments(command, flags.Args(), relationType, explicit["type"]); err != nil {
		return err
	}
	imageQuery := explicit["image"]
	if imageQuery && strings.TrimSpace(imagePath) == "" {
		return errors.New("--image requires a local JPEG or PNG path")
	}
	if imageQuery && explicit["mode"] {
		return errors.New("--mode applies to text-only searches; omit it with --image")
	}
	if explicit["id"] && ((command == "media" && strings.TrimSpace(mediaID) == "") || (command == "observations" && strings.TrimSpace(observationID) == "")) || explicit["output"] && (strings.TrimSpace(outputPath) == "" || mediaID == "") {
		return errors.New("--id must not be empty; --output requires --id and a new file path")
	}
	needsArgument := command == "search" || command == "similar" || command == "get" || command == "media" || command == "observations"
	if useObservations && imageQuery && flags.NArg() == 0 {
		return errors.New("--observations requires a text query")
	}
	if command == "search" {
		if !imageQuery && flags.NArg() == 0 {
			return errors.New("search requires query words or --image PATH")
		}
	} else if !isResearchCommand(command) && (needsArgument && flags.NArg() != 1 || !needsArgument && flags.NArg() != 0) {
		return errors.New("incorrect arguments (see pipeline --help)")
	}

	if mode != "hybrid" && mode != "vector" {
		return errors.New("mode must be hybrid or vector")
	}
	if format != "json" && format != "markdown" && format != "html" && format != "source" {
		return errors.New("format must be json, markdown, html, or source")
	}
	options.Page.Size = limit
	if err := options.Page.Validate(); err != nil {
		return err
	}
	value := flags.Arg(0)
	if command == "search" {
		value = strings.Join(flags.Args(), " ")
	}
	if needsArgument && (!imageQuery || flags.NArg() > 0) && strings.TrimSpace(value) == "" {
		return errors.New("query or ID must not be empty")
	}
	if command == "search" && len([]rune(value)) > 1000 {
		return errors.New("query exceeds 1000 characters")
	}
	bundle, err := files.Open("data/dataset.zip")
	if err != nil {
		return errors.New("this development build has no dataset; build a bundle or download a release binary")
	}
	defer bundle.Close()
	info, err := bundle.Stat()
	if err != nil {
		return err
	}
	reader, ok := bundle.(io.ReaderAt)
	if !ok {
		data, err := io.ReadAll(bundle)
		if err != nil {
			return err
		}
		reader = bytes.NewReader(data)
	}
	d, err := dataset.OpenReader(reader, info.Size())
	if err != nil {
		return err
	}
	if command == "search" || command == "similar" || command == "list" {
		filter, err = d.PrepareFilter(filter)
		if err != nil {
			return err
		}
	}
	if useObservations && !d.HasObservations() {
		return errors.New("this dataset has no generated observations")
	}
	options.Mode, options.Observations, options.Filter = mode, useObservations, filter
	output := json.NewEncoder(out)
	output.SetIndent("", "  ")
	if isResearchCommand(command) {
		return runResearch(d, command, flags.Args(), relationType, filter, limit, output)
	}
	switch command {
	case "info":
		kindSet := map[string]bool{}
		for _, entity := range d.Entities {
			kindSet[entity.Kind] = true
		}
		kinds := make([]string, 0, len(kindSet))
		for kind := range kindSet {
			kinds = append(kinds, kind)
		}
		sort.Strings(kinds)
		var imageModel json.RawMessage
		calibration := "unavailable"
		if d.HasImages() {
			imageModel, err = d.ImageModel()
			if err != nil {
				return err
			}
			calibration = "uncalibrated"
		}
		response := map[string]any{"version": version, "dataset_id": d.Manifest.DatasetID, "content_sha256": d.Manifest.ContentSHA256, "entities": d.Manifest.Entities, "evidence_pages": d.Manifest.EvidencePages, "chunks": d.Manifest.Chunks, "model": d.Manifest.Model, "sources": d.Manifest.Sources, "image_available": d.HasImages(), "image": d.Manifest.Image, "image_model": imageModel, "image_calibration_status": calibration, "observations_available": d.HasObservations(), "observations": d.Manifest.Observations, "search": d.Manifest.Search}
		response["kinds"] = kinds
		if d.Manifest.Research != nil {
			response["research_available"], response["research"] = true, d.Manifest.Research
		}
		if d.HasCalibration() {
			response["calibration"] = d.Manifest.Calibration
		}
		return output.Encode(response)
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
		return searchImage(ctx, d, imagePath, value, filter, options, useObservations, output)
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
	if mode == "hybrid" {
		if err := d.PrepareTextSearch(useObservations); err != nil {
			return err
		}
	}
	encoder, err := embedding.New(ctx, d.Files)
	if err != nil {
		return err
	}
	vector, err := encoder.Encode(ctx, value)
	closeErr := encoder.Close()
	if err != nil {
		return err
	}
	if closeErr != nil {
		return closeErr
	}
	if useObservations {
		page, err := d.SearchObservationsPage(vector, value, mode == "hybrid", filter, options.Page)
		if err != nil {
			return err
		}
		response := map[string]any{"dataset_id": d.Manifest.DatasetID, "query": value, "query_type": "text", "mode": mode, "observations": true, "match_status": "no_supported_match", "calibration_status": "uncalibrated", "results": page.Results}
		if mode == "hybrid" && d.Manifest.Search != nil {
			response["ranking_policy"], response["score_kind"] = d.Manifest.Search, "ranking_signal"
		}
		return writeSearchPage(d, response, "text", options, page, visualCalibrationCandidates(page.Leaders), output)
	}
	page, err := d.SearchPage(vector, value, mode == "hybrid", filter, options.Page)
	if err != nil {
		return err
	}
	contributions, err := textResults(d, page.Results)
	if err != nil {
		return err
	}
	var leaders []textResult
	if d.HasCalibration() {
		leaders, err = textResults(d, page.Leaders)
		if err != nil {
			return err
		}
	}
	results := page.Leaders

	status := "candidates"
	if len(results) == 0 {
		status = "no_supported_match"
	}
	response := map[string]any{"dataset_id": d.Manifest.DatasetID, "query": value, "query_type": "text", "match_status": status, "mode": mode, "results": contributions}
	if mode == "hybrid" && d.Manifest.Search != nil {
		response["ranking_policy"] = d.Manifest.Search
		response["score_kind"] = "ranking_signal"
		response["calibration_status"] = "uncalibrated"
		if len(results) == 0 || (!results[0].NameMatch && results[0].Ranking.LexicalRank == 0) {
			response["match_status"] = "no_supported_match"
		}
	}
	selected := dataset.RankedPage[textResult]{Results: contributions, Total: page.Total, HasMore: page.HasMore}
	return writeSearchPage(d, response, "text", options, selected, textCandidates(leaders), output)
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
