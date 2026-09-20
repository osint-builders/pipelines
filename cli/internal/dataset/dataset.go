// Package dataset reads immutable bundles and performs exact cosine nearest-neighbor search.
package dataset

import (
	"archive/zip"
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"math"
	"sort"
	"strings"
	"unicode"
)

type Model struct {
	ID         string `json:"id"`
	Revision   string `json:"revision"`
	Dimensions int    `json:"dimensions"`
}
type Manifest struct {
	FormatVersion int               `json:"format_version"`
	DatasetID     string            `json:"dataset_id"`
	ContentSHA256 string            `json:"content_sha256"`
	RecipeSHA256  string            `json:"recipe_sha256"`
	Entities      int               `json:"entities"`
	EvidencePages int               `json:"evidence_pages"`
	Chunks        int               `json:"chunks"`
	Model         Model             `json:"model"`
	Sources       []string          `json:"sources"`
	Files         map[string]string `json:"files"`
}
type Entity struct {
	ID         string   `json:"id"`
	Title      string   `json:"title"`
	URL        string   `json:"url"`
	Source     string   `json:"source"`
	Kind       string   `json:"kind"`
	Categories []string `json:"categories"`
	Aliases    []string `json:"aliases"`
}
type Chunk struct {
	Entity     int    `json:"entity"`
	Text       string `json:"text"`
	EvidenceID string `json:"evidence_id"`
}
type Result struct {
	Entity
	Score      float64 `json:"score"`
	Cosine     float64 `json:"cosine"`
	NameMatch  bool    `json:"name_match"`
	Snippet    string  `json:"snippet"`
	EvidenceID string  `json:"evidence_id"`
}
type Filter struct{ Source, Kind, Category string }
type Dataset struct {
	Files    *zip.Reader
	Manifest Manifest
	Entities []Entity
	chunks   []Chunk
	vectors  []float32
	byID     map[string]int
}

func Open(data []byte) (*Dataset, error) {
	files, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return nil, err
	}
	d := &Dataset{Files: files, byID: map[string]int{}}
	raw, err := fs.ReadFile(files, "manifest.json")
	if err != nil {
		return nil, err
	}
	if err = json.Unmarshal(raw, &d.Manifest); err != nil {
		return nil, err
	}
	if d.Manifest.FormatVersion != 2 || d.Manifest.Model.Dimensions != 384 {
		return nil, errors.New("unsupported dataset format or embedding dimensions")
	}
	if len(d.Manifest.DatasetID) != 64 {
		return nil, errors.New("invalid dataset ID")
	}
	raw, err = d.Read("index.json")
	if err != nil {
		return nil, err
	}
	if err = json.Unmarshal(raw, &d.Entities); err != nil {
		return nil, err
	}
	if len(d.Entities) == 0 || len(d.Entities) != d.Manifest.Entities {
		return nil, errors.New("entity count mismatch")
	}
	for i, entity := range d.Entities {
		parts := strings.Split(entity.ID, ":")
		if len(parts) != 2 || parts[0] != entity.Source || !safeSource(parts[0]) || !safeKey(parts[1]) {
			return nil, errors.New("invalid entity ID")
		}
		if !validKind(entity.Kind) {
			return nil, errors.New("invalid entity kind")
		}
		if _, exists := d.byID[entity.ID]; exists {
			return nil, errors.New("duplicate entity ID")
		}
		d.byID[entity.ID] = i
	}
	return d, nil
}

func safeSource(source string) bool {
	if source == "" {
		return false
	}
	for _, r := range source {
		if !(r >= 'a' && r <= 'z' || r >= '0' && r <= '9' || r == '_' || r == '-') {
			return false
		}
	}
	return true
}

func (d *Dataset) Read(name string) ([]byte, error) {
	expected, ok := d.Manifest.Files[name]
	if !ok {
		return nil, fmt.Errorf("missing manifest entry: %s", name)
	}
	raw, err := fs.ReadFile(d.Files, name)
	if err != nil {
		return nil, err
	}
	actual := sha256.Sum256(raw)
	if hex.EncodeToString(actual[:]) != expected {
		return nil, fmt.Errorf("checksum mismatch: %s", name)
	}
	return raw, nil
}

func (d *Dataset) Verify() error {
	for name := range d.Manifest.Files {
		if _, err := d.Read(name); err != nil {
			return err
		}
	}
	if err := d.LoadVectors(); err != nil {
		return err
	}
	pages := make([]map[string]bool, len(d.Entities))
	allPages := map[string]bool{}
	for i, entity := range d.Entities {
		raw, err := d.Export(entity.ID, "json", "")
		if err != nil {
			return err
		}
		var record struct{ Evidence []struct{ ID string } }
		if err := json.Unmarshal(raw, &record); err != nil {
			return err
		}
		pages[i] = map[string]bool{}
		for _, page := range record.Evidence {
			pages[i][page.ID] = true
			allPages[entity.Source+":"+page.ID] = true
		}
	}
	if len(allPages) != d.Manifest.EvidencePages {
		return errors.New("evidence page count mismatch")
	}
	seenIdentity := map[int]bool{}
	for _, chunk := range d.chunks {
		if !seenIdentity[chunk.Entity] && chunk.EvidenceID != "" {
			return errors.New("first chunk must identify the entity")
		}
		seenIdentity[chunk.Entity] = true
		if chunk.EvidenceID != "" && !pages[chunk.Entity][chunk.EvidenceID] {
			return errors.New("chunk references unrelated evidence")
		}
	}
	return nil
}

func (d *Dataset) LoadVectors() error {
	if d.vectors != nil {
		return nil
	}
	raw, err := d.Read("chunks.json")
	if err != nil {
		return err
	}
	var chunks []Chunk
	if err := json.Unmarshal(raw, &chunks); err != nil {
		return err
	}
	raw, err = d.Read("vectors.f32")
	if err != nil {
		return err
	}
	dim := d.Manifest.Model.Dimensions
	if len(chunks) != d.Manifest.Chunks || len(raw) != len(chunks)*dim*4 {
		return errors.New("vector count mismatch")
	}
	vectors := make([]float32, len(raw)/4)
	seen := make([]bool, len(d.Entities))
	for i, chunk := range chunks {
		if chunk.Entity < 0 || chunk.Entity >= len(d.Entities) {
			return errors.New("invalid chunk entity")
		}
		seen[chunk.Entity] = true
		var norm float64
		for j := range dim {
			offset := i*dim + j
			value := math.Float32frombits(binary.LittleEndian.Uint32(raw[offset*4:]))
			if math.IsNaN(float64(value)) || math.IsInf(float64(value), 0) {
				return errors.New("non-finite vector")
			}
			vectors[offset] = value
			norm += float64(value) * float64(value)
		}
		if math.Abs(norm-1) > 0.002 {
			return errors.New("vector is not normalized")
		}
	}
	for _, present := range seen {
		if !present {
			return errors.New("entity has no embedding")
		}
	}
	d.chunks, d.vectors = chunks, vectors
	return nil
}

func (d *Dataset) EntityVector(id string) ([]float32, error) {
	index, exists := d.byID[id]
	if !exists {
		return nil, fmt.Errorf("unknown entity ID: %s", id)
	}
	if err := d.LoadVectors(); err != nil {
		return nil, err
	}
	// The first chunk is the entity's identity embedding, stable across body length changes.
	for i, chunk := range d.chunks {
		if chunk.Entity == index {
			start := i * d.Manifest.Model.Dimensions
			return d.vectors[start : start+d.Manifest.Model.Dimensions], nil
		}
	}
	return nil, errors.New("entity has no embedding")
}

func key(text string) string {
	return strings.Map(func(r rune) rune {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			return unicode.ToLower(r)
		}
		return -1
	}, text)
}

func nameMatch(query string, aliases []string) bool {
	joined := key(query)
	words := strings.FieldsFunc(strings.ToLower(query), func(r rune) bool { return !unicode.IsLetter(r) && !unicode.IsDigit(r) })
	for _, alias := range aliases {
		normalized := key(alias)
		if normalized == "" {
			continue
		}
		if normalized == joined || len([]rune(normalized)) >= 5 && strings.Contains(joined, normalized) {
			return true
		}
		for _, word := range words {
			if word == normalized {
				return true
			}
		}
	}
	return false
}

func (f Filter) matches(e Entity) bool {
	if f.Source != "" && f.Source != e.Source || f.Kind != "" && f.Kind != e.Kind {
		return false
	}
	if f.Category == "" {
		return true
	}
	for _, category := range e.Categories {
		if category == f.Category {
			return true
		}
	}
	return false
}

func (d *Dataset) Search(vector []float32, query string, hybrid bool, filter Filter, limit int, exclude string) ([]Result, error) {
	if len(vector) != d.Manifest.Model.Dimensions {
		return nil, errors.New("query vector dimension mismatch")
	}
	if limit < 1 || limit > 100 {
		return nil, errors.New("limit must be between 1 and 100")
	}
	var norm float64
	for _, v := range vector {
		norm += float64(v) * float64(v)
	}
	if math.IsNaN(norm) || math.IsInf(norm, 0) || math.Abs(norm-1) > 0.002 {
		return nil, errors.New("query vector must be finite and normalized")
	}
	if err := d.LoadVectors(); err != nil {
		return nil, err
	}
	best := make(map[int]Result)
	dim := d.Manifest.Model.Dimensions
	for i, chunk := range d.chunks {
		entity := d.Entities[chunk.Entity]
		if entity.ID == exclude || !filter.matches(entity) {
			continue
		}
		var cosine float64
		for j, value := range vector {
			cosine += float64(value) * float64(d.vectors[i*dim+j])
		}
		previous, exists := best[chunk.Entity]
		if !exists || cosine > previous.Cosine {
			best[chunk.Entity] = Result{Entity: entity, Score: cosine, Cosine: cosine, Snippet: chunk.Text, EvidenceID: chunk.EvidenceID}
		}
	}
	results := make([]Result, 0, len(best))
	for _, result := range best {
		if hybrid && nameMatch(query, append([]string{result.Title}, result.Aliases...)) {
			result.NameMatch = true
			result.Score += 2
		}
		results = append(results, result)
	}
	sort.Slice(results, func(i, j int) bool {
		if results[i].Score == results[j].Score {
			return results[i].ID < results[j].ID
		}
		return results[i].Score > results[j].Score
	})
	if len(results) > limit {
		results = results[:limit]
	}
	return results, nil
}

func DecodeVectors(raw []byte, count, dimensions int) ([][]float32, error) {
	if len(raw) != count*dimensions*4 {
		return nil, errors.New("invalid reference vectors")
	}
	vectors := make([][]float32, count)
	for i := range count {
		vectors[i] = make([]float32, dimensions)
		for j := range dimensions {
			vectors[i][j] = math.Float32frombits(binary.LittleEndian.Uint32(raw[(i*dimensions+j)*4:]))
		}
	}
	return vectors, nil
}
