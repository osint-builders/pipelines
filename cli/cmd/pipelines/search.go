package main

import (
	"encoding/json"
	"errors"

	"github.com/osint-builders/pipelines/cli/internal/dataset"
)

type searchOptions struct {
	Page         dataset.Page
	Raw          bool
	Mode         string
	Observations bool
	Filter       dataset.Filter
}

type textResult struct {
	dataset.Result
	Matches []dataset.Match `json:"matches"`
}

func textResults(d *dataset.Dataset, rows []dataset.Result) ([]textResult, error) {
	results := make([]textResult, len(rows))
	for i, result := range rows {
		matches, err := d.TextMatches(result)
		if err != nil {
			return nil, err
		}
		results[i] = textResult{result, matches}
	}
	return results, nil
}

func textCandidates(rows []textResult) []dataset.CalibrationCandidate {
	results := make([]dataset.CalibrationCandidate, len(rows))
	for i := range rows {
		results[i] = dataset.CalibrationCandidate{Score: rows[i].Score, Cosine: &rows[i].Cosine, Matches: rows[i].Matches}
	}
	return results
}

func writeSearchPage[T any](d *dataset.Dataset, response map[string]any, queryType string, options searchOptions, page dataset.RankedPage[T], leaders []dataset.CalibrationCandidate, output *json.Encoder) error {
	// Decisions describe the query's global leaders, including on later pages.
	if err := completeSearch(d, response, queryType, options.Mode, options.Observations, options.Filter, page.Results, leaders, options.Page.Size); err != nil {
		return err
	}
	response["results"] = page.Results
	response["page"], response["limit"], response["total"], response["has_more"] = options.Page.Number, options.Page.Size, page.Total, page.HasMore
	if options.Raw {
		rows, err := withRawContent(d, page.Results)
		if err != nil {
			return err
		}
		response["results"] = rows
	}
	return output.Encode(response)
}

// Decode only the selected page to attach original records without coupling
// content export to the several ranking result types.
func withRawContent(d *dataset.Dataset, results any) ([]map[string]json.RawMessage, error) {
	body, err := json.Marshal(results)
	if err != nil {
		return nil, err
	}
	var rows []map[string]json.RawMessage
	if err := json.Unmarshal(body, &rows); err != nil {
		return nil, err
	}
	for _, row := range rows {
		var id string
		if err := json.Unmarshal(row["id"], &id); err != nil || id == "" {
			return nil, errors.New("search result has no entity ID")
		}
		content, err := d.Export(id, "json", "")
		if err != nil {
			return nil, err
		}
		row["content"] = content
	}
	return rows, nil
}
