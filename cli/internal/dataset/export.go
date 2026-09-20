package dataset

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"unicode/utf8"
)

func safeKey(value string) bool {
	if len(value) < 1 || len(value) > 128 {
		return false
	}
	for i, r := range value {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || i > 0 && (r == '.' || r == '_' || r == '-')) {
			return false
		}
	}
	return true
}

func validKind(kind string) bool {
	switch kind {
	case "radar", "emitter", "sensor", "vehicle", "aircraft", "vessel", "weapon", "equipment", "item", "site", "spacecraft":
		return true
	}
	return false
}

// Export follows an entity's evidence references without assuming one page per item.
func (d *Dataset) Export(id, format, evidenceID string) ([]byte, error) {
	i, exists := d.byID[id]
	if !exists {
		return nil, fmt.Errorf("unknown entity ID: %s", id)
	}
	entity := d.Entities[i]
	raw, err := d.Read("entities/" + strings.Replace(id, ":", "/", 1) + ".json")
	if err != nil {
		return nil, err
	}
	var record map[string]any
	if err = json.Unmarshal(raw, &record); err != nil {
		return nil, err
	}
	if record["id"] != id || record["source"] != entity.Source || record["kind"] != entity.Kind {
		return nil, errors.New("entity metadata mismatch")
	}
	pages, ok := record["evidence"].([]any)
	if !ok || len(pages) == 0 {
		return nil, errors.New("entity has no evidence")
	}
	var selected []any
	var markdown []string
	var html []byte
	seen := map[string]bool{}
	for _, value := range pages {
		page, ok := value.(map[string]any)
		if !ok {
			return nil, errors.New("invalid evidence")
		}
		pageID, ok := page["id"].(string)
		url, urlOK := page["url"].(string)
		digest := sha256.Sum256([]byte(url))
		if !ok || !urlOK || pageID != hex.EncodeToString(digest[:])[:24] || seen[pageID] {
			return nil, errors.New("invalid evidence identity")
		}
		seen[pageID] = true
		if evidenceID != "" && pageID != evidenceID {
			continue
		}
		text, ok := page["markdown"].(string)
		if !ok || text == "" {
			return nil, errors.New("missing evidence Markdown")
		}
		html, err = d.Read("html/" + entity.Source + "/" + pageID + ".html")
		if err != nil {
			return nil, err
		}
		hash := sha256.Sum256(html)
		if hex.EncodeToString(hash[:]) != page["html_sha256"] {
			return nil, errors.New("evidence HTML checksum mismatch")
		}
		if utf8.Valid(html) {
			page["html"] = string(html)
		} else {
			page["html_base64"] = base64.StdEncoding.EncodeToString(html)
		}
		selected = append(selected, page)
		markdown = append(markdown, text)
	}
	if len(selected) == 0 {
		return nil, errors.New("evidence page does not belong to entity")
	}
	switch format {
	case "html":
		if len(selected) != 1 {
			return nil, errors.New("entity has multiple evidence pages; select one with --evidence PAGE_ID")
		}
		return html, nil
	case "markdown":
		return []byte(strings.Join(markdown, "\n\n---\n\n")), nil
	case "json":
		record["evidence"], record["dataset_id"] = selected, d.Manifest.DatasetID
		raw, err = json.MarshalIndent(record, "", "  ")
		return append(raw, '\n'), err
	default:
		return nil, errors.New("unknown export format")
	}
}
