package dataset

import (
	"encoding/json"
	"errors"
	"sort"
	"strings"
)

type Match struct {
	Method               string   `json:"method,omitempty"`
	Terms                []string `json:"terms,omitempty"`
	Reason               string   `json:"reason,omitempty"`
	Channel              string   `json:"channel"`
	Score                float64  `json:"score"`
	EvidenceID           string   `json:"evidence_id"`
	URL                  string   `json:"url"`
	MediaID              string   `json:"media_id,omitempty"`
	ClaimID              string   `json:"claim_id,omitempty"`
	ModelSHA256          string   `json:"model_sha256,omitempty"`
	Origin               string   `json:"origin,omitempty"`
	ObservationID        string   `json:"observation_id,omitempty"`
	RecipeSHA256         string   `json:"recipe_sha256,omitempty"`
	ModelID              string   `json:"model_id,omitempty"`
	ModelRevision        string   `json:"model_revision,omitempty"`
	EmbeddingModelSHA256 string   `json:"embedding_model_sha256,omitempty"`
}

func (d *Dataset) entityEvidence(index int) (map[string]string, error) {
	if pages := d.evidenceURLs[index]; pages != nil {
		return pages, nil
	}
	entity := d.Entities[index]
	raw, err := d.Read("entities/" + strings.Replace(entity.ID, ":", "/", 1) + ".json")
	if err != nil {
		return nil, err
	}
	var record struct {
		ID, Source, Kind string
		Evidence         []struct {
			ID, URL  string
			RecordID string `json:"record_id"`
		}
	}
	if err := json.Unmarshal(raw, &record); err != nil {
		return nil, err
	}
	if record.ID != entity.ID || record.Source != entity.Source || record.Kind != entity.Kind || len(record.Evidence) == 0 {
		return nil, errors.New("invalid media entity record")
	}
	pages := map[string]string{}
	for _, page := range record.Evidence {
		identity := page.URL
		if page.RecordID != "" {
			identity += "\n" + page.RecordID
		}
		if !validHTTPURL(page.URL) || page.ID != digestString([]byte(identity))[:24] || pages[page.ID] != "" {
			return nil, errors.New("invalid media evidence identity")
		}
		pages[page.ID] = page.URL
	}
	if d.evidenceURLs == nil {
		d.evidenceURLs = map[int]map[string]string{}
	}
	d.evidenceURLs[index] = pages
	return pages, nil
}

// TextMatch resolves contribution provenance without changing legacy result fields.
func (d *Dataset) TextMatch(result Result) (Match, error) {
	index, ok := d.byID[result.ID]
	if !ok {
		return Match{}, errors.New("text result references an unknown entity")
	}
	pages, err := d.entityEvidence(index)
	if err != nil {
		return Match{}, err
	}
	evidence := result.EvidenceID
	if evidence == "" {
		ids := make([]string, 0, len(pages))
		for id := range pages {
			ids = append(ids, id)
		}
		sort.Strings(ids)
		evidence = ids[0]
		for _, id := range ids {
			if pages[id] == d.Entities[index].URL {
				evidence = id
				break
			}
		}
	}
	if pages[evidence] == "" {
		return Match{}, errors.New("text match references unrelated evidence")
	}
	return Match{Channel: "text", Score: result.Score, EvidenceID: evidence, URL: pages[evidence]}, nil
}
