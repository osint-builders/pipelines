package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
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
  pipelines similar [--limit 10] [filters] SOURCE:ID
  pipelines get [--format json|markdown|html|source] [--evidence PAGE_ID] SOURCE:ID
  pipelines info
  pipelines verify
  pipelines version
  pipelines notices

Filters: --source SOURCE --kind KIND --category CATEGORY
Place flags before the query or ID. Every search returns stable, source-qualified IDs.
JSON is the default output. Source export preserves the archived response bytes.
All commands work offline. This executable never scrapes or downloads models.
`

func main() {
	if err := run(context.Background(), os.Args[1:], os.Stdout); err != nil {
		_ = json.NewEncoder(os.Stderr).Encode(map[string]string{"error": err.Error()})
		os.Exit(1)
	}
}

func run(ctx context.Context, args []string, out io.Writer) error {
	if len(args) == 0 || args[0] == "--help" || args[0] == "help" || args[0] == "-h" {
		_, err := io.WriteString(out, help)
		return err
	}
	if args[0] == "version" {
		return json.NewEncoder(out).Encode(map[string]string{"version": version})
	}
	if args[0] == "notices" {
		for _, name := range []string{"data/MODEL-LICENSE.txt", "data/THIRD-PARTY-NOTICES.txt"} {
			body, err := assets.Files.ReadFile(name)
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
	if command != "search" && command != "similar" && command != "get" && command != "info" && command != "verify" {
		return fmt.Errorf("unknown command: %s", command)
	}
	flags := flag.NewFlagSet(command, flag.ContinueOnError)
	flags.SetOutput(io.Discard)
	limit := 10
	mode, format, evidence := "hybrid", "json", ""
	filter := dataset.Filter{}
	if command == "search" || command == "similar" {
		flags.IntVar(&limit, "limit", 10, "maximum results")
		flags.StringVar(&filter.Source, "source", "", "source filter")
		flags.StringVar(&filter.Kind, "kind", "", "kind filter")
		flags.StringVar(&filter.Category, "category", "", "category filter")
	}
	if command == "search" {
		flags.StringVar(&mode, "mode", "hybrid", "search mode")
	}
	if command == "get" {
		flags.StringVar(&format, "format", "json", "output format")
		flags.StringVar(&evidence, "evidence", "", "select an evidence page")
	}
	if err := flags.Parse(args[1:]); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			_, err = io.WriteString(out, help)
			return err
		}
		return err
	}
	needsArgument := command == "search" || command == "similar" || command == "get"
	if needsArgument && flags.NArg() != 1 || !needsArgument && flags.NArg() != 0 {
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
	if needsArgument && strings.TrimSpace(value) == "" {
		return errors.New("query or ID must not be empty")
	}
	if command == "search" && len([]rune(value)) > 1000 {
		return errors.New("query exceeds 1000 characters")
	}
	data, err := assets.Files.ReadFile("data/dataset.zip")
	if err != nil {
		return errors.New("this development build has no dataset; build a bundle or download a release binary")
	}
	d, err := dataset.Open(data)
	if err != nil {
		return err
	}
	output := json.NewEncoder(out)
	output.SetIndent("", "  ")
	switch command {
	case "info":
		return output.Encode(map[string]any{"version": version, "dataset_id": d.Manifest.DatasetID, "content_sha256": d.Manifest.ContentSHA256, "entities": d.Manifest.Entities, "evidence_pages": d.Manifest.EvidencePages, "chunks": d.Manifest.Chunks, "model": d.Manifest.Model, "sources": d.Manifest.Sources})
	case "get":
		raw, err := d.Export(value, format, evidence)
		if err != nil {
			return err
		}
		_, err = out.Write(raw)
		return err
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
	encoder, err := embedding.New(ctx, d.Files)
	if err != nil {
		return err
	}
	defer encoder.Close()
	if command == "verify" {
		// Producer and consumer must use the same embedding space, including tokenization.
		raw, err := d.Read("probes.json")
		if err != nil {
			return err
		}
		var probes []string
		if err = json.Unmarshal(raw, &probes); err != nil {
			return err
		}
		raw, err = d.Read("probes.f32")
		if err != nil {
			return err
		}
		reference, err := dataset.DecodeVectors(raw, len(probes), d.Manifest.Model.Dimensions)
		if err != nil {
			return err
		}
		for i, probe := range probes {
			actual, err := encoder.Encode(ctx, probe)
			if err != nil {
				return err
			}
			var errorSum float64
			for j, v := range actual {
				delta := float64(v - reference[i][j])
				errorSum += delta * delta
			}
			if math.IsNaN(errorSum) || math.Sqrt(errorSum) > 0.001 {
				return fmt.Errorf("embedding parity failed for probe %d: L2 error %g", i, math.Sqrt(errorSum))
			}
		}
		return output.Encode(map[string]any{"ok": true, "dataset_id": d.Manifest.DatasetID, "entities": d.Manifest.Entities, "evidence_pages": d.Manifest.EvidencePages, "probes": len(probes)})
	}
	vector, err := encoder.Encode(ctx, value)
	if err != nil {
		return err
	}
	results, err := d.Search(vector, value, mode == "hybrid", filter, limit, "")
	if err != nil {
		return err
	}
	return output.Encode(map[string]any{"dataset_id": d.Manifest.DatasetID, "query": value, "mode": mode, "results": results})
}
