package dataset

import (
	"bytes"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"regexp"
	"sort"
	"strings"
	"unicode/utf8"
)

type ObservationManifest struct {
	SchemaVersion        int    `json:"schema_version"`
	Records              int    `json:"records"`
	Chunks               int    `json:"chunks"`
	Dimensions           int    `json:"dimensions"`
	VectorDType          string `json:"vector_dtype"`
	EmbeddingModelSHA256 string `json:"embedding_model_sha256"`
	IndexSHA256          string `json:"index_sha256"`
	GallerySHA256        string `json:"gallery_sha256"`
	Search               struct {
		Aggregation string          `json:"aggregation"`
		Calibration json.RawMessage `json:"calibration"`
	} `json:"search"`
}

type ObservationReference struct {
	MediaID    string `json:"media_id"`
	EntityID   string `json:"entity_id"`
	EvidenceID string `json:"evidence_id"`
}

type ObservationRegion struct {
	Text       string      `json:"text"`
	Confidence *float64    `json:"confidence"`
	Polygon    [][]float64 `json:"polygon"`
}

type Observation struct {
	ID           string                 `json:"id"`
	Kind         string                 `json:"kind"`
	Origin       string                 `json:"origin"`
	MediaSHA256  string                 `json:"media_sha256"`
	MediaIDs     []string               `json:"media_ids"`
	References   []ObservationReference `json:"references"`
	RecipeSHA256 string                 `json:"recipe_sha256"`
	Text         string                 `json:"text"`
	Confidence   *float64               `json:"confidence"`
	Regions      []ObservationRegion    `json:"regions"`
}

type ObservationSet struct {
	Observations []json.RawMessage          `json:"observations"`
	Recipes      map[string]json.RawMessage `json:"recipes"`
}

type ObservationProbe struct {
	Text   string    `json:"text"`
	Vector []float32 `json:"vector"`
}

type observationChunk struct {
	ObservationID string `json:"observation_id"`
	Text          string `json:"text"`
	VectorIndex   int    `json:"vector_index"`
}

type observationRecipe struct {
	Version       string                     `json:"version"`
	ModelID       string                     `json:"model_id"`
	ModelRevision string                     `json:"model_revision"`
	Settings      map[string]json.RawMessage `json:"settings"`
}

type observationData struct {
	rows       []Observation
	rawRows    []json.RawMessage
	byID       map[string]int
	recipes    map[string]json.RawMessage
	provenance map[string]observationRecipe
	chunks     []observationChunk
	vectors    []float32
	probes     []ObservationProbe
}

func (d *Dataset) HasObservations() bool { return d.Manifest.Observations != nil }

// Raw values preserve the producer's canonical UTF-8 strings and numeric lexemes.
// In particular, confidence 1.0 must not become 1 when verifying its identity.
func rawObject(body []byte) (map[string]json.RawMessage, error) {
	decoder := json.NewDecoder(bytes.NewReader(body))
	start, err := decoder.Token()
	if err != nil || start != json.Delim('{') {
		return nil, errors.New("expected a JSON object")
	}
	values := map[string]json.RawMessage{}
	for decoder.More() {
		token, err := decoder.Token()
		if err != nil {
			return nil, err
		}
		key, ok := token.(string)
		if !ok {
			return nil, errors.New("invalid JSON object key")
		}
		if _, exists := values[key]; exists {
			return nil, errors.New("duplicate JSON object key")
		}
		var value json.RawMessage
		if err := decoder.Decode(&value); err != nil {
			return nil, err
		}
		values[key] = value
	}
	if _, err := decoder.Token(); err != nil {
		return nil, err
	}
	if _, err := decoder.Token(); err != io.EOF {
		return nil, errors.New("trailing JSON content")
	}
	return values, nil
}

func observationIdentity(raw map[string]json.RawMessage) (string, error) {
	var body bytes.Buffer
	body.WriteByte('{')
	for i, key := range []string{"confidence", "kind", "media_sha256", "recipe_sha256", "regions", "text"} {
		value, ok := raw[key]
		if !ok {
			return "", errors.New("observation identity field is missing")
		}
		if i > 0 {
			body.WriteByte(',')
		}
		body.WriteByte('"')
		body.WriteString(key)
		body.WriteString(`":`)
		body.Write(value)
	}
	body.WriteByte('}')
	return digestString(body.Bytes()), nil
}

func boundedText(text string, maximum int) bool {
	return utf8.ValidString(text) && strings.TrimSpace(text) != "" && utf8.RuneCountInString(text) <= maximum
}

func validConfidence(value *float64) bool {
	return value == nil || (!math.IsNaN(*value) && !math.IsInf(*value, 0) && *value >= 0 && *value <= 1)
}

func hasFields(fields map[string]json.RawMessage, names ...string) bool {
	if len(fields) != len(names) {
		return false
	}
	for _, name := range names {
		if _, ok := fields[name]; !ok {
			return false
		}
	}
	return true
}

func (d *Dataset) LoadObservations() error {
	if d.observations != nil {
		return nil
	}
	if !d.HasObservations() {
		return errors.New("this dataset has no generated observations")
	}
	if err := d.LoadImages(); err != nil {
		return err
	}
	m := d.Manifest.Observations
	if m.SchemaVersion != 1 || m.Records < 0 || m.Records > 20000 || m.Chunks < 0 || m.Chunks > 100000 ||
		m.Dimensions != 384 || m.VectorDType != "float32-le" || !validDigest(m.EmbeddingModelSHA256) ||
		m.EmbeddingModelSHA256 != d.Manifest.Files["model/model.onnx"] || !validDigest(m.IndexSHA256) ||
		m.GallerySHA256 != d.Manifest.Image.GallerySHA256 || m.Search.Aggregation != "max-v1" || string(m.Search.Calibration) != "null" {
		return errors.New("invalid observation manifest")
	}
	allowed := map[string]uint64{"observations/index.json": 64 << 20, "observations/recipes.json": 8 << 20,
		"observations/chunks.json": 64 << 20, "observations/vectors.f32": 384 * 4 * 100000,
		"observations/report.json": 16 << 20, "observations/probes.json": 1 << 20}
	for member := range d.Manifest.Files {
		if strings.HasPrefix(member, "observations/") && allowed[member] == 0 {
			return errors.New("undeclared observation artifact")
		}
	}
	members := map[string][]byte{}
	for member, maximum := range allowed {
		body, err := d.readImage(member, maximum)
		if err != nil {
			return err
		}
		members[member] = body
	}
	if digestString(members["observations/index.json"]) != m.IndexSHA256 {
		return errors.New("observation index hash mismatch")
	}
	if _, err := rawObject(members["observations/report.json"]); err != nil {
		return fmt.Errorf("invalid observation report: %w", err)
	}
	loaded := &observationData{byID: map[string]int{}, provenance: map[string]observationRecipe{}}
	var err error
	loaded.recipes, err = rawObject(members["observations/recipes.json"])
	if err != nil {
		return err
	}
	if len(loaded.recipes) == 0 {
		return errors.New("observation recipes are missing")
	}
	for hash, raw := range loaded.recipes {
		if !validDigest(hash) || digestString(raw) != hash {
			return errors.New("observation recipe hash mismatch")
		}
		fields, err := rawObject(raw)
		if err != nil {
			return err
		}
		if !hasFields(fields, "version", "model_id", "model_revision", "settings") {
			return errors.New("unsupported observation recipe fields")
		}
		var recipe observationRecipe
		if err := json.Unmarshal(raw, &recipe); err != nil {
			return err
		}
		if strings.TrimSpace(recipe.Version) == "" || strings.TrimSpace(recipe.ModelID) == "" || strings.TrimSpace(recipe.ModelRevision) == "" || recipe.Settings == nil {
			return errors.New("incomplete observation recipe")
		}
		loaded.provenance[hash] = recipe
	}
	var rawRows []json.RawMessage
	if err := json.Unmarshal(members["observations/index.json"], &rawRows); err != nil {
		return err
	}
	if len(rawRows) != m.Records || bytes.Equal(members["observations/index.json"], []byte("null")) {
		return errors.New("observation record count mismatch")
	}
	loaded.rawRows = rawRows
	media := map[string]MediaRecord{}
	for _, record := range d.images.records {
		media[record.ID] = record
	}
	for _, raw := range rawRows {
		fields, err := rawObject(raw)
		if err != nil {
			return err
		}
		if !hasFields(fields, "id", "kind", "origin", "media_sha256", "media_ids", "references", "recipe_sha256", "text", "confidence", "regions") {
			return errors.New("invalid observation fields")
		}
		var row Observation
		if err := json.Unmarshal(raw, &row); err != nil {
			return err
		}
		identity, err := observationIdentity(fields)
		if err != nil {
			return err
		}
		if !validDigest(row.ID) || row.ID != identity || (len(loaded.rows) > 0 && loaded.rows[len(loaded.rows)-1].ID >= row.ID) ||
			(row.Kind != "ocr" && row.Kind != "description") || row.Origin != "generated" || !validDigest(row.MediaSHA256) ||
			loaded.recipes[row.RecipeSHA256] == nil || !boundedText(row.Text, 16384) || !validConfidence(row.Confidence) ||
			len(row.Regions) > 256 || len(row.MediaIDs) == 0 || len(row.References) == 0 || len(fields["regions"]) == 0 || fields["regions"][0] != '[' {
			return errors.New("invalid generated observation")
		}
		if row.Kind == "description" && (row.Confidence != nil || len(row.Regions) != 0) {
			return errors.New("descriptions cannot claim confidence or OCR regions")
		}
		var regions []json.RawMessage
		if err := json.Unmarshal(fields["regions"], &regions); err != nil {
			return err
		}
		for i, region := range row.Regions {
			regionFields, err := rawObject(regions[i])
			if err != nil || !hasFields(regionFields, "text", "confidence", "polygon") {
				return errors.New("invalid OCR region fields")
			}
			var coordinates [][]*float64
			if err := json.Unmarshal(regionFields["polygon"], &coordinates); err != nil {
				return err
			}
			if !boundedText(region.Text, 16384) || !validConfidence(region.Confidence) || len(region.Polygon) != 4 {
				return errors.New("invalid OCR region")
			}
			for i, point := range region.Polygon {
				if len(point) != 2 {
					return errors.New("OCR polygon point must have two coordinates")
				}
				for j, coordinate := range point {
					if coordinates[i][j] == nil || math.IsNaN(coordinate) || math.IsInf(coordinate, 0) || coordinate < 0 || coordinate > 1 {
						return errors.New("OCR polygon is outside the image")
					}
				}
			}
			var area float64
			for i, point := range region.Polygon {
				next := region.Polygon[(i+1)%4]
				area += point[0]*next[1] - next[0]*point[1]
			}
			if math.Abs(area) < 1e-12 {
				return errors.New("OCR polygon has no area")
			}
		}
		if row.Kind == "ocr" {
			texts := make([]string, len(row.Regions))
			for i, region := range row.Regions {
				texts[i] = region.Text
			}
			if strings.Join(texts, "\n") != row.Text {
				return errors.New("OCR text must be the joined image regions")
			}
		}
		expected := map[ObservationReference]bool{}
		for i, id := range row.MediaIDs {
			record, ok := media[id]
			if !ok || record.VectorIndex == nil || record.SHA256 != row.MediaSHA256 || (i > 0 && row.MediaIDs[i-1] >= id) {
				return errors.New("observation references an unrelated or unindexed image")
			}
			for _, ref := range record.References {
				expected[ObservationReference{id, ref.EntityID, ref.EvidenceID}] = true
			}
		}
		var references []json.RawMessage
		if err := json.Unmarshal(fields["references"], &references); err != nil {
			return err
		}
		for i, ref := range row.References {
			refFields, err := rawObject(references[i])
			if err != nil || !hasFields(refFields, "media_id", "entity_id", "evidence_id") {
				return errors.New("invalid observation reference fields")
			}
			if !expected[ref] {
				return errors.New("observation has unrelated or repeated evidence")
			}
			delete(expected, ref)
		}
		if len(expected) > 0 {
			return errors.New("observation omitted current image associations")
		}
		loaded.byID[row.ID] = len(loaded.rows)
		loaded.rows = append(loaded.rows, row)
	}
	if err := json.Unmarshal(members["observations/chunks.json"], &loaded.chunks); err != nil {
		return err
	}
	var rawChunks []json.RawMessage
	if err := json.Unmarshal(members["observations/chunks.json"], &rawChunks); err != nil || rawChunks == nil {
		return errors.New("observation chunks must be an array")
	}
	if len(loaded.chunks) != m.Chunks || len(members["observations/vectors.f32"]) != m.Chunks*384*4 {
		return errors.New("observation vector count mismatch")
	}
	seen := map[string]bool{}
	loaded.vectors = make([]float32, m.Chunks*384)
	for i, chunk := range loaded.chunks {
		fields, err := rawObject(rawChunks[i])
		if err != nil || !hasFields(fields, "observation_id", "text", "vector_index") || bytes.Equal(fields["vector_index"], []byte("null")) {
			return errors.New("invalid observation chunk fields")
		}
		rowIndex, ok := loaded.byID[chunk.ObservationID]
		if !ok || chunk.VectorIndex != i || !boundedText(chunk.Text, 16384) || !strings.Contains(loaded.rows[rowIndex].Text, chunk.Text) ||
			(i > 0 && loaded.chunks[i-1].ObservationID > chunk.ObservationID) {
			return errors.New("invalid observation chunk identity or order")
		}
		seen[chunk.ObservationID] = true
		vector := loaded.vectors[i*384 : (i+1)*384]
		for j := range vector {
			vector[j] = math.Float32frombits(binary.LittleEndian.Uint32(members["observations/vectors.f32"][(i*384+j)*4:]))
		}
		if err := unitVector(vector, 384); err != nil {
			return err
		}
	}
	if len(seen) != len(loaded.rows) {
		return errors.New("observation has no text vector")
	}
	if err := validateObservationReport(members["observations/report.json"], loaded, media, m.GallerySHA256); err != nil {
		return err
	}
	if err := json.Unmarshal(members["observations/probes.json"], &loaded.probes); err != nil {
		return err
	}
	if len(loaded.probes) < 3 || len(loaded.probes) > 100 {
		return errors.New("observation verification requires three to one hundred probes")
	}
	var rawProbes []struct {
		Vector []*float32 `json:"vector"`
	}
	if err := json.Unmarshal(members["observations/probes.json"], &rawProbes); err != nil {
		return err
	}
	seenProbes := map[string]bool{}
	for i, probe := range loaded.probes {
		if !boundedText(probe.Text, 1000) || seenProbes[probe.Text] {
			return errors.New("invalid or duplicate observation probe")
		}
		if err := unitVector(probe.Vector, 384); err != nil {
			return err
		}
		for _, component := range rawProbes[i].Vector {
			if component == nil {
				return errors.New("observation probe contains a null component")
			}
		}
		seenProbes[probe.Text] = true
	}
	d.observations = loaded
	return nil
}

func validateObservationReport(body []byte, loaded *observationData, media map[string]MediaRecord, gallerySHA256 string) error {
	fields, err := rawObject(body)
	if err != nil {
		return err
	}
	var header struct {
		SchemaVersion int            `json:"schema_version"`
		GallerySHA256 string         `json:"gallery_sha256"`
		Counts        map[string]int `json:"counts"`
	}
	if err := json.Unmarshal(body, &header); err != nil || header.SchemaVersion != 1 || header.GallerySHA256 != gallerySHA256 || header.Counts == nil {
		return errors.New("invalid observation report identity or counts")
	}
	var kinds []string
	if err := json.Unmarshal(fields["kinds"], &kinds); err != nil || len(kinds) < 1 || len(kinds) > 2 {
		return errors.New("observation report requires analysis kinds")
	}
	wanted := map[string]string{}
	for i, kind := range kinds {
		if (kind != "description" && kind != "ocr") || (i > 0 && kinds[i-1] >= kind) {
			return errors.New("invalid observation report kind order")
		}
		for id, row := range media {
			if row.VectorIndex != nil {
				wanted[id+"\x00"+kind] = ""
			}
		}
	}
	for _, row := range loaded.rows {
		for _, id := range row.MediaIDs {
			key := id + "\x00" + row.Kind
			existing, ok := wanted[key]
			if !ok || existing != "" {
				return errors.New("observation report omits or repeats an analysis")
			}
			wanted[key] = row.ID
		}
	}
	var outcomes []json.RawMessage
	if len(fields["outcomes"]) == 0 || fields["outcomes"][0] != '[' || json.Unmarshal(fields["outcomes"], &outcomes) != nil {
		return errors.New("observation report requires outcomes")
	}
	errorClass := regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]{0,127}$`)
	counts := map[string]int{}
	for _, raw := range outcomes {
		outcomeFields, err := rawObject(raw)
		if err != nil {
			return err
		}
		var outcome struct {
			MediaID       string `json:"media_id"`
			Kind          string `json:"kind"`
			State         string `json:"state"`
			ObservationID string `json:"observation_id"`
			Reason        string `json:"reason"`
		}
		if err := json.Unmarshal(raw, &outcome); err != nil {
			return err
		}
		names := []string{"media_id", "kind", "state"}
		if outcome.State == "observed" {
			names = append(names, "observation_id")
		} else if outcome.State == "failed" {
			names = append(names, "reason")
		}
		if !hasFields(outcomeFields, names...) {
			return errors.New("invalid observation outcome fields")
		}
		key := outcome.MediaID + "\x00" + outcome.Kind
		expected, ok := wanted[key]
		if !ok {
			return errors.New("observation report has an unrelated or repeated outcome")
		}
		if outcome.State == "observed" {
			if expected == "" || outcome.ObservationID != expected {
				return errors.New("observation report has an incorrect observation identity")
			}
		} else {
			_, hasID := outcomeFields["observation_id"]
			if expected != "" || hasID || (outcome.State != "empty" && outcome.State != "failed" && outcome.State != "selection") {
				return errors.New("invalid non-observed analysis outcome")
			}
			if outcome.State == "failed" && !errorClass.MatchString(outcome.Reason) {
				return errors.New("failed observation outcome requires an error class")
			}
		}
		if _, exists := outcomeFields["reason"]; exists && outcome.State != "failed" {
			return errors.New("only failed observation outcomes have a reason")
		}
		delete(wanted, key)
		counts[outcome.State]++
	}
	if len(wanted) != 0 {
		return errors.New("observation report omits analysis outcomes")
	}
	if len(counts) != len(header.Counts) {
		return errors.New("observation report outcome counts mismatch")
	}
	for state, count := range counts {
		if header.Counts[state] != count {
			return errors.New("observation report outcome counts mismatch")
		}
	}
	return nil
}

func (d *Dataset) Observations(entityID, observationID string) (ObservationSet, error) {
	result := ObservationSet{Observations: []json.RawMessage{}, Recipes: map[string]json.RawMessage{}}
	if _, ok := d.byID[entityID]; !ok {
		return result, errors.New("unknown entity ID")
	}
	if err := d.LoadObservations(); err != nil {
		return result, err
	}
	for index, row := range d.observations.rows {
		if observationID != "" && row.ID != observationID {
			continue
		}
		for _, ref := range row.References {
			if ref.EntityID == entityID {
				result.Observations = append(result.Observations, append(json.RawMessage(nil), d.observations.rawRows[index]...))
				result.Recipes[row.RecipeSHA256] = append(json.RawMessage(nil), d.observations.recipes[row.RecipeSHA256]...)
				break
			}
		}
	}
	if observationID != "" && len(result.Observations) == 0 {
		return result, errors.New("observation does not belong to entity")
	}
	return result, nil
}

func (d *Dataset) ObservationProbes() ([]ObservationProbe, error) {
	if err := d.LoadObservations(); err != nil {
		return nil, err
	}
	probes := make([]ObservationProbe, len(d.observations.probes))
	for i, probe := range d.observations.probes {
		probes[i] = probe
		probes[i].Vector = append([]float32(nil), probe.Vector...)
	}
	return probes, nil
}

func (d *Dataset) observedText(vector []float32, query string, hybrid bool, filter Filter) ([]Result, map[string]Match, error) {
	if err := d.LoadObservations(); err != nil {
		return nil, nil, err
	}
	source, err := d.search(vector, query, false, filter, len(d.Entities), "")
	if err != nil {
		return nil, nil, err
	}
	best := map[string]Result{}
	generated := map[string]Match{}
	for _, result := range source {
		result.Cosine = math.Max(-1, math.Min(1, result.Cosine))
		result.Score = result.Cosine
		best[result.ID] = result
	}
	for _, chunk := range d.observations.chunks {
		row := d.observations.rows[d.observations.byID[chunk.ObservationID]]
		var cosine float64
		for j, value := range vector {
			cosine += float64(value) * float64(d.observations.vectors[chunk.VectorIndex*384+j])
		}
		cosine = math.Max(-1, math.Min(1, cosine))
		for _, ref := range row.References {
			previous, ok := best[ref.EntityID]
			if !ok {
				continue
			}
			old, derived := generated[ref.EntityID]
			if cosine < previous.Cosine || (cosine == previous.Cosine && (!derived || old.ObservationID < row.ID ||
				(old.ObservationID == row.ID && (old.MediaID < ref.MediaID || (old.MediaID == ref.MediaID && old.EvidenceID <= ref.EvidenceID))))) {
				continue
			}
			previous.Cosine, previous.Score, previous.Snippet, previous.EvidenceID = cosine, cosine, chunk.Text, ref.EvidenceID
			best[ref.EntityID] = previous
			recipe := d.observations.provenance[row.RecipeSHA256]
			generated[ref.EntityID] = Match{Channel: row.Kind, Score: cosine, Origin: "generated", ObservationID: row.ID, MediaID: ref.MediaID,
				EvidenceID: ref.EvidenceID, URL: d.images.evidence[d.byID[ref.EntityID]][ref.EvidenceID], RecipeSHA256: row.RecipeSHA256,
				ModelID: recipe.ModelID, ModelRevision: recipe.ModelRevision, EmbeddingModelSHA256: d.Manifest.Observations.EmbeddingModelSHA256}
		}
	}
	results := make([]Result, 0, len(best))
	for _, result := range best {
		if hybrid && d.Manifest.Search == nil && nameMatch(query, append([]string{result.Title}, result.Aliases...)) {
			result.NameMatch = true
			result.Score += 2
		}
		if match, ok := generated[result.ID]; ok {
			match.Score = result.Score
			generated[result.ID] = match
		}
		results = append(results, result)
	}
	if hybrid && d.Manifest.Search != nil {
		results, err = d.rankText(results, generated, query, true)
		return results, generated, err
	}
	sort.Slice(results, func(i, j int) bool {
		if results[i].Score != results[j].Score {
			return results[i].Score > results[j].Score
		}
		if results[i].NameMatch != results[j].NameMatch {
			return results[i].NameMatch
		}
		return results[i].ID < results[j].ID
	})
	return results, generated, nil
}

func (d *Dataset) SearchObservations(vector []float32, query string, hybrid bool, filter Filter, limit int) ([]VisualResult, error) {
	if limit < 1 || limit > 100 {
		return nil, errors.New("limit must be between 1 and 100")
	}
	results, generated, err := d.observedText(vector, query, hybrid, filter)
	if err != nil {
		return nil, err
	}
	if len(results) > limit {
		results = results[:limit]
	}
	output := make([]VisualResult, len(results))
	for i, result := range results {
		if result.Ranking != nil {
			matches, err := d.TextMatches(result)
			if err != nil {
				return nil, err
			}
			output[i] = VisualResult{Entity: result.Entity, Score: result.Score, Cosine: &result.Cosine, NameMatch: result.NameMatch, Snippet: result.Snippet, EvidenceID: result.EvidenceID, Matches: matches, Ranking: result.Ranking}
			continue
		}
		match, derived := generated[result.ID]
		if !derived {
			match, err = d.TextMatch(result)
			if err != nil {
				return nil, err
			}
		}
		output[i] = VisualResult{Entity: result.Entity, Score: result.Score, Cosine: &result.Cosine, NameMatch: result.NameMatch, Snippet: result.Snippet, EvidenceID: result.EvidenceID, Matches: []Match{match}}
	}
	return output, nil
}
