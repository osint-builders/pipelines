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
	var sourceBody []byte
	seen := map[string]bool{}
	for _, value := range pages {
		page, ok := value.(map[string]any)
		if !ok {
			return nil, errors.New("invalid evidence")
		}
		pageID, ok := page["id"].(string)
		url, urlOK := page["url"].(string)
		identity := url
		if id, exists := page["record_id"]; exists {
			recordID, valid := id.(string)
			records, listOK := page["records"].([]any)
			if !valid || !safeKey(recordID) || !listOK || len(records) == 0 || page["html_origin"] != "record-rendered" {
				return nil, errors.New("invalid structured record evidence")
			}
			identity += "\n" + recordID
		}
		digest := sha256.Sum256([]byte(identity))
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
		sourceBody = html
		if page["html_origin"] == "record-rendered" {
			response, valid := page["source_response"].(map[string]any)
			contentType, typeOK := response["content_type"].(string)
			urlHash := sha256.Sum256([]byte(url))
			member := "responses/" + entity.Source + "/" + hex.EncodeToString(urlHash[:])[:24] + ".json"
			expected, exists := d.Manifest.Files[member]
			if page["record_id"] == nil || !valid || !typeOK || contentType == "" || response["url"] != url || response["body_member"] != member || response["body_base64"] != nil || !exists || response["sha256"] != expected {
				return nil, errors.New("invalid record response provenance")
			}
			if d.sourceResponses == nil {
				d.sourceResponses = map[string][]byte{}
			}
			if d.sourceResponses[member] == nil {
				d.sourceResponses[member], err = d.Read(member)
				if err != nil {
					return nil, err
				}
			}
			sourceBody = d.sourceResponses[member]
		} else if page["html_origin"] == "api-rendered" {
			response, ok := page["source_response"].(map[string]any)
			contentType, typeOK := response["content_type"].(string)
			if !ok || response["url"] != url || !typeOK || contentType == "" {
				return nil, errors.New("invalid API response provenance")
			}
			encoded, ok := response["body_base64"].(string)
			if !ok {
				return nil, errors.New("missing API response body")
			}
			body, err := base64.StdEncoding.Strict().DecodeString(encoded)
			if err != nil {
				return nil, errors.New("invalid API response body")
			}
			digest := sha256.Sum256(body)
			if hex.EncodeToString(digest[:]) != response["sha256"] {
				return nil, errors.New("API response checksum mismatch")
			}
			sourceBody = body
		} else if page["source_response"] != nil || page["html_origin"] != nil {
			return nil, errors.New("unexpected API response provenance")
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
	case "source":
		if len(selected) != 1 {
			return nil, errors.New("entity has multiple evidence pages; select one with --evidence PAGE_ID")
		}
		return sourceBody, nil
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
