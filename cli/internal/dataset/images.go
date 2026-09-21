package dataset

import (
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"image"
	"math"
	"net/url"
	"path"
	"regexp"
	"sort"
	"strings"

	"github.com/gomlx/compute/dtypes/float16"
	"github.com/osint-builders/pipelines/cli/internal/imagepreprocess"
)

type ImageSearch struct {
	Fusion       string          `json:"fusion"`
	RankConstant int             `json:"rank_constant"`
	Calibration  json.RawMessage `json:"calibration"`
}

type ImageManifest struct {
	SchemaVersion int         `json:"schema_version"`
	ModelSHA256   string      `json:"model_sha256"`
	Dimensions    int         `json:"dimensions"`
	VectorDType   string      `json:"vector_dtype"`
	Vectors       int         `json:"vectors"`
	Records       int         `json:"records"`
	PreviewBytes  int64       `json:"preview_bytes"`
	GallerySHA256 string      `json:"gallery_sha256"`
	Search        ImageSearch `json:"search"`
}

type MediaReference struct {
	EntityID    string `json:"entity_id"`
	EvidenceID  string `json:"evidence_id"`
	Caption     string `json:"caption"`
	Section     string `json:"section"`
	Ambiguous   bool   `json:"ambiguous,omitempty"`
	Association string `json:"association,omitempty"`
}

type MediaPreview struct {
	Member      string `json:"member"`
	SHA256      string `json:"sha256"`
	ContentType string `json:"content_type"`
	Width       int    `json:"width"`
	Height      int    `json:"height"`
}

type MediaRecord struct {
	ID              string           `json:"id"`
	Source          string           `json:"source"`
	URL             string           `json:"url"`
	OriginalURL     string           `json:"original_url"`
	SHA256          string           `json:"sha256"`
	ContentType     string           `json:"content_type"`
	Width           int              `json:"width"`
	Height          int              `json:"height"`
	Caption         string           `json:"caption"`
	References      []MediaReference `json:"references"`
	VectorIndex     *int             `json:"vector_index"`
	ExclusionReason string           `json:"exclusion_reason"`
	Preview         *MediaPreview    `json:"preview"`
}

type ImageProbe struct {
	ID          string    `json:"id"`
	ImageMember string    `json:"image_member"`
	Vector      []float32 `json:"vector"`
}

type Match struct {
	Method               string   `json:"method,omitempty"`
	Terms                []string `json:"terms,omitempty"`
	Reason               string   `json:"reason,omitempty"`
	Channel              string   `json:"channel"`
	Score                float64  `json:"score"`
	EvidenceID           string   `json:"evidence_id"`
	URL                  string   `json:"url"`
	MediaID              string   `json:"media_id,omitempty"`
	ModelSHA256          string   `json:"model_sha256,omitempty"`
	Origin               string   `json:"origin,omitempty"`
	ObservationID        string   `json:"observation_id,omitempty"`
	RecipeSHA256         string   `json:"recipe_sha256,omitempty"`
	ModelID              string   `json:"model_id,omitempty"`
	ModelRevision        string   `json:"model_revision,omitempty"`
	EmbeddingModelSHA256 string   `json:"embedding_model_sha256,omitempty"`
}

type VisualResult struct {
	Entity
	Score      float64      `json:"score"`
	Cosine     *float64     `json:"cosine"`
	NameMatch  bool         `json:"name_match"`
	Snippet    string       `json:"snippet"`
	EvidenceID string       `json:"evidence_id"`
	Matches    []Match      `json:"matches"`
	Ranking    *TextRanking `json:"ranking,omitempty"`
}

type imageData struct {
	records  []MediaRecord
	vectors  []float32
	probes   []ImageProbe
	evidence map[int]map[string]string
}

func (d *Dataset) HasImages() bool { return d.Manifest.Image != nil }

func digestString(body []byte) string {
	digest := sha256.Sum256(body)
	return hex.EncodeToString(digest[:])
}

func validDigest(value string) bool {
	decoded, err := hex.DecodeString(value)
	return err == nil && len(decoded) == 32 && value == strings.ToLower(value)
}

func validImageURL(value string) bool {
	parsed, err := url.Parse(value)
	return err == nil && (parsed.Scheme == "https" || parsed.Scheme == "http") && parsed.Host != "" && parsed.User == nil
}

func (d *Dataset) readImage(name string, maximum uint64) ([]byte, error) {
	member := d.members[name]
	if member == nil || member.UncompressedSize64 > maximum {
		return nil, fmt.Errorf("missing or oversized bundle member: %s", name)
	}
	return d.Read(name)
}

// ImageModel verifies the self-contained graph and its preprocessing contract.
// ONNX graph execution is checked by the runtime using ImageProbes.
func (d *Dataset) ImageModel() ([]byte, error) {
	if !d.HasImages() {
		return nil, errors.New("dataset has no image model")
	}
	raw, err := d.readImage("image/model.json", 1<<20)
	if err != nil {
		return nil, err
	}
	var lock struct {
		SchemaVersion int                    `json:"schema_version"`
		File          string                 `json:"file"`
		SHA256        string                 `json:"sha256"`
		Bytes         int64                  `json:"bytes"`
		Input         string                 `json:"input"`
		Output        string                 `json:"output"`
		Shape         []int                  `json:"shape"`
		Dimensions    int                    `json:"dimensions"`
		Normalization string                 `json:"normalization"`
		Preprocess    imagepreprocess.Recipe `json:"preprocess"`
	}
	if err := json.Unmarshal(raw, &lock); err != nil {
		return nil, err
	}
	if lock.SchemaVersion != 1 || !regexp.MustCompile(`^[a-zA-Z0-9_.-]+\.onnx$`).MatchString(lock.File) ||
		!validDigest(lock.SHA256) || lock.SHA256 != d.Manifest.Image.ModelSHA256 ||
		lock.Dimensions != d.Manifest.Image.Dimensions || lock.Normalization != "l2" ||
		lock.Input == "" || lock.Output == "" || lock.Bytes < 1 || lock.Bytes > 512<<20 ||
		len(lock.Shape) != 4 || lock.Shape[0] != 1 || lock.Shape[1] != 3 ||
		lock.Shape[2] != lock.Preprocess.Size || lock.Shape[3] != lock.Preprocess.Size {
		return nil, errors.New("invalid image model contract")
	}
	if err := lock.Preprocess.Validate(); err != nil {
		return nil, err
	}
	graph, err := d.readImage("image/"+lock.File, 512<<20)
	if err != nil {
		return nil, err
	}
	if int64(len(graph)) != lock.Bytes || digestString(graph) != lock.SHA256 {
		return nil, errors.New("image model hash or size mismatch")
	}
	return raw, nil
}

func (d *Dataset) LoadImages() error {
	if d.images != nil {
		return nil
	}
	if !d.HasImages() {
		return errors.New("dataset has no image index")
	}
	m := d.Manifest.Image
	if m.SchemaVersion != 1 || m.Dimensions != 512 || m.VectorDType != "float16-le" ||
		m.Vectors < 0 || m.Vectors > 10000 || m.Records < 0 || m.PreviewBytes < 0 || m.PreviewBytes > 32<<20 ||
		!validDigest(m.ModelSHA256) || !validDigest(m.GallerySHA256) ||
		m.Search.Fusion != "rrf-v1" || m.Search.RankConstant != 60 || string(m.Search.Calibration) != "null" {
		return errors.New("invalid image index manifest")
	}
	if !validDigest(d.Manifest.ContentSHA256) || !validDigest(d.Manifest.RecipeSHA256) ||
		digestString([]byte(d.Manifest.ContentSHA256+d.Manifest.RecipeSHA256)) != d.Manifest.DatasetID {
		return errors.New("image dataset identity mismatch")
	}
	for name := range d.members {
		if name != "manifest.json" && d.Manifest.Files[name] == "" {
			return errors.New("bundle contains an undeclared member")
		}
	}
	if _, err := d.ImageModel(); err != nil {
		return err
	}
	raw, err := d.readImage("image/index.json", 64<<20)
	if err != nil {
		return err
	}
	if digestString(raw) != m.GallerySHA256 {
		return errors.New("image gallery identity mismatch")
	}
	loaded := &imageData{evidence: map[int]map[string]string{}}
	if err := json.Unmarshal(raw, &loaded.records); err != nil {
		return err
	}
	if len(loaded.records) != m.Records {
		return errors.New("media record count mismatch")
	}
	raw, err = d.readImage("image/vectors.f16", 10000*512*2)
	if err != nil {
		return err
	}
	if len(raw) != m.Vectors*m.Dimensions*2 {
		return errors.New("image vector count mismatch")
	}
	loaded.vectors = make([]float32, m.Vectors*m.Dimensions)
	for i := range m.Vectors {
		vector := loaded.vectors[i*m.Dimensions : (i+1)*m.Dimensions]
		for j := range vector {
			vector[j] = float16.FromBits(binary.LittleEndian.Uint16(raw[(i*m.Dimensions+j)*2:])).Float32()
		}
		if err := unitVector(vector, m.Dimensions); err != nil {
			return err
		}
		var norm float64
		for _, value := range vector {
			norm += float64(value) * float64(value)
		}
		norm = math.Sqrt(norm)
		for j := range vector {
			vector[j] = float32(float64(vector[j]) / norm)
		}
	}
	vectorHashes, hashVectors := map[int]string{}, map[string]int{}
	originals := map[string]struct {
		MIME          string
		Width, Height int
	}{}
	sources := map[string]bool{}
	for _, source := range d.Manifest.Sources {
		sources[source] = true
	}
	views := map[int]map[int]bool{}
	previews := map[string]MediaPreview{}
	var previewBytes int64
	for i, record := range loaded.records {
		if i > 0 && loaded.records[i-1].ID >= record.ID {
			return errors.New("media records are not uniquely sorted")
		}
		if !safeSource(record.Source) || !sources[record.Source] || !validImageURL(record.URL) ||
			record.ID != record.Source+":media:"+digestString([]byte(record.URL))[:24] ||
			(record.OriginalURL != "" && !validImageURL(record.OriginalURL)) ||
			!validDigest(record.SHA256) || record.Width < 1 || record.Height < 1 ||
			record.Width > imagepreprocess.MaxImagePixels || record.Height > imagepreprocess.MaxImagePixels ||
			int64(record.Width)*int64(record.Height) > imagepreprocess.MaxImagePixels ||
			(record.ContentType != "image/jpeg" && record.ContentType != "image/png" && record.ContentType != "image/webp") {
			return errors.New("invalid original media metadata")
		}
		metadata := struct {
			MIME          string
			Width, Height int
		}{record.ContentType, record.Width, record.Height}
		if previous, ok := originals[record.SHA256]; ok && previous != metadata {
			return errors.New("inconsistent original media metadata")
		}
		originals[record.SHA256] = metadata
		if record.VectorIndex == nil {
			if record.ExclusionReason == "" {
				return errors.New("unindexed media requires an exclusion reason")
			}
		} else {
			v := *record.VectorIndex
			if v < 0 || v >= m.Vectors || record.ExclusionReason != "" || record.Preview == nil || len(record.References) == 0 {
				return errors.New("invalid indexed media")
			}
			if hash, ok := vectorHashes[v]; ok && hash != record.SHA256 {
				return errors.New("image vector shared by different originals")
			}
			if index, ok := hashVectors[record.SHA256]; ok && index != v {
				return errors.New("original media has duplicate vectors")
			}
			vectorHashes[v], hashVectors[record.SHA256] = record.SHA256, v
		}
		seenReferences := map[MediaReference]bool{}
		for _, ref := range record.References {
			if seenReferences[ref] {
				return errors.New("duplicate media reference")
			}
			seenReferences[ref] = true
			entity, ok := d.byID[ref.EntityID]
			if !ok || d.Entities[entity].Source != record.Source {
				return errors.New("media references an unrelated entity")
			}
			if loaded.evidence[entity] == nil {
				loaded.evidence[entity], err = d.imageEvidence(entity)
				if err != nil {
					return err
				}
			}
			if loaded.evidence[entity][ref.EvidenceID] == "" {
				return errors.New("media references unrelated evidence")
			}
			if record.VectorIndex != nil {
				if views[entity] == nil {
					views[entity] = map[int]bool{}
				}
				views[entity][*record.VectorIndex] = true
				if len(views[entity]) > 8 {
					return errors.New("entity exceeds image view limit")
				}
			}
		}
		if record.Preview != nil {
			preview := *record.Preview
			extension := ".jpg"
			if preview.ContentType == "image/png" {
				extension = ".png"
			}
			if !validDigest(preview.SHA256) || preview.Member != "image/previews/"+preview.SHA256+extension {
				return errors.New("invalid media preview identity")
			}
			if previous, ok := previews[preview.Member]; ok {
				if previous != preview {
					return errors.New("inconsistent shared preview metadata")
				}
				continue
			}
			member := d.members[preview.Member]
			if member == nil || d.Manifest.Files[preview.Member] != preview.SHA256 ||
				member.UncompressedSize64 > 32<<20 || preview.Width < 1 || preview.Height < 1 || preview.Width > 512 || preview.Height > 512 ||
				(preview.ContentType != "image/jpeg" && preview.ContentType != "image/png") {
				return errors.New("invalid preview member or metadata")
			}
			previews[preview.Member] = preview
			previewBytes += int64(member.UncompressedSize64)
			if previewBytes > m.PreviewBytes {
				return errors.New("preview byte budget exceeded")
			}
		}
	}
	if len(vectorHashes) != m.Vectors || previewBytes != m.PreviewBytes {
		return errors.New("orphan image vector or preview count mismatch")
	}
	loaded.probes, err = d.loadImageProbes()
	if err != nil {
		return err
	}
	probeMembers := map[string]bool{}
	for _, probe := range loaded.probes {
		probeMembers[probe.ImageMember] = true
	}
	for member := range d.Manifest.Files {
		if strings.HasPrefix(member, "image/previews/") {
			if _, ok := previews[member]; !ok {
				return errors.New("orphan image preview")
			}
		}
		if strings.HasPrefix(member, "image/probes/") && !probeMembers[member] {
			return errors.New("orphan image probe")
		}
	}
	d.images = loaded
	return nil
}

func (d *Dataset) imageEvidence(index int) (map[string]string, error) {
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
		if !validImageURL(page.URL) || page.ID != digestString([]byte(identity))[:24] || pages[page.ID] != "" {
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
	pages, err := d.imageEvidence(index)
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

func (d *Dataset) verifyImages() error {
	if err := d.LoadImages(); err != nil {
		return err
	}
	seen := map[string]bool{}
	for _, record := range d.images.records {
		if record.Preview == nil || seen[record.Preview.Member] {
			continue
		}
		if _, err := d.previewBody(*record.Preview); err != nil {
			return err
		}
		seen[record.Preview.Member] = true
	}
	return nil
}

func (d *Dataset) previewBody(preview MediaPreview) ([]byte, error) {
	body, err := d.readImage(preview.Member, 32<<20)
	if err != nil {
		return nil, err
	}
	if digestString(body) != preview.SHA256 {
		return nil, errors.New("preview checksum mismatch")
	}
	if err := validatePixels(body, preview.ContentType, preview.Width, preview.Height); err != nil {
		return nil, err
	}
	return body, nil
}

func validatePixels(body []byte, mime string, width, height int) error {
	if width < 1 || height < 1 || int64(width)*int64(height) > imagepreprocess.MaxImagePixels {
		return errors.New("invalid preview dimensions")
	}
	config, format, err := image.DecodeConfig(bytes.NewReader(body))
	if err != nil || (format != "jpeg" && format != "png") || "image/"+format != mime || config.Width != width || config.Height != height {
		return errors.New("preview pixels do not match metadata")
	}
	_, _, err = image.Decode(bytes.NewReader(body))
	return err
}

func unitVector(vector []float32, dimensions int) error {
	if len(vector) != dimensions {
		return errors.New("image vector dimension mismatch")
	}
	var norm float64
	for _, value := range vector {
		norm += float64(value) * float64(value)
	}
	if math.IsNaN(norm) || math.IsInf(norm, 0) || math.Abs(norm-1) > 0.002 {
		return errors.New("image vector must be finite and normalized")
	}
	return nil
}

func (d *Dataset) loadImageProbes() ([]ImageProbe, error) {
	raw, err := d.readImage("image/probes.json", 8<<20)
	if err != nil {
		return nil, err
	}
	var probes []ImageProbe
	if err := json.Unmarshal(raw, &probes); err != nil {
		return nil, err
	}
	if len(probes) < 3 || len(probes) > 100 {
		return nil, errors.New("invalid image probe count")
	}
	seen := map[string]bool{}
	for _, probe := range probes {
		extension := path.Ext(probe.ImageMember)
		if !safeKey(probe.ID) || seen[probe.ID] || (extension != ".jpg" && extension != ".png") ||
			probe.ImageMember != "image/probes/"+probe.ID+extension {
			return nil, errors.New("invalid image probe identity")
		}
		seen[probe.ID] = true
		if err := unitVector(probe.Vector, d.Manifest.Image.Dimensions); err != nil {
			return nil, err
		}
		body, err := d.readImage(probe.ImageMember, 1<<20)
		if err != nil {
			return nil, err
		}
		config, format, err := image.DecodeConfig(bytes.NewReader(body))
		if err != nil || (extension == ".jpg" && format != "jpeg") || (extension == ".png" && format != "png") {
			return nil, errors.New("invalid image probe pixels")
		}
		if err := validatePixels(body, "image/"+format, config.Width, config.Height); err != nil {
			return nil, err
		}
	}
	return probes, nil
}

func (d *Dataset) ImageProbes() ([]ImageProbe, error) {
	if err := d.LoadImages(); err != nil {
		return nil, err
	}
	probes := make([]ImageProbe, len(d.images.probes))
	for i, probe := range d.images.probes {
		probes[i] = probe
		probes[i].Vector = append([]float32(nil), probe.Vector...)
	}
	return probes, nil
}

func (d *Dataset) Media(entityID, mediaID string) ([]MediaRecord, error) {
	if _, ok := d.byID[entityID]; !ok {
		return nil, fmt.Errorf("unknown entity ID: %s", entityID)
	}
	results := []MediaRecord{}
	if d.HasImages() {
		if err := d.LoadImages(); err != nil {
			return nil, err
		}
		for _, record := range d.images.records {
			if mediaID != "" && record.ID != mediaID {
				continue
			}
			for _, ref := range record.References {
				if ref.EntityID == entityID {
					copy := record
					copy.References = append([]MediaReference(nil), record.References...)
					if record.Preview != nil {
						preview := *record.Preview
						copy.Preview = &preview
					}
					if record.VectorIndex != nil {
						index := *record.VectorIndex
						copy.VectorIndex = &index
					}
					results = append(results, copy)
					break
				}
			}
		}
	}
	if mediaID != "" && len(results) == 0 {
		return nil, errors.New("media does not belong to entity")
	}
	return results, nil
}

func (d *Dataset) MediaPreview(entityID, mediaID string) ([]byte, error) {
	if mediaID == "" {
		return nil, errors.New("select a media ID to export a preview")
	}
	records, err := d.Media(entityID, mediaID)
	if err != nil {
		return nil, err
	}
	if records[0].Preview == nil {
		return nil, errors.New("media has no embedded preview")
	}
	return d.previewBody(*records[0].Preview)
}

func (d *Dataset) SearchImages(imageVector, textVector []float32, query string, filter Filter, limit int) ([]VisualResult, error) {
	return d.searchImages(imageVector, textVector, query, filter, limit, false)
}

func (d *Dataset) SearchImagesWithObservations(imageVector, textVector []float32, query string, filter Filter, limit int) ([]VisualResult, error) {
	if len(textVector) == 0 {
		return nil, errors.New("observations require a text query")
	}
	return d.searchImages(imageVector, textVector, query, filter, limit, true)
}

func (d *Dataset) searchImages(imageVector, textVector []float32, query string, filter Filter, limit int, observations bool) ([]VisualResult, error) {
	if limit < 1 || limit > 100 {
		return nil, errors.New("limit must be between 1 and 100")
	}
	var err error
	filter, err = d.PrepareFilter(filter)
	if err != nil {
		return nil, err
	}
	if err := d.LoadImages(); err != nil {
		return nil, err
	}
	if err := unitVector(imageVector, d.Manifest.Image.Dimensions); err != nil {
		return nil, err
	}
	best := map[int]VisualResult{}
	for _, record := range d.images.records {
		if record.VectorIndex == nil {
			continue
		}
		var cosine float64
		for j, value := range imageVector {
			cosine += float64(value) * float64(d.images.vectors[*record.VectorIndex*d.Manifest.Image.Dimensions+j])
		}
		cosine = math.Max(-1, math.Min(1, cosine))
		for _, ref := range record.References {
			index := d.byID[ref.EntityID]
			entity := d.Entities[index]
			if !filter.matches(entity) {
				continue
			}
			previous, exists := best[index]
			if exists && (previous.Score > cosine || (previous.Score == cosine &&
				(previous.Matches[0].MediaID < record.ID || (previous.Matches[0].MediaID == record.ID && previous.EvidenceID <= ref.EvidenceID)))) {
				continue
			}
			caption := ref.Caption
			if caption == "" {
				caption = record.Caption
			}
			best[index] = VisualResult{Entity: entity, Score: cosine, Snippet: caption, EvidenceID: ref.EvidenceID,
				Matches: []Match{{Channel: "image", Score: cosine, EvidenceID: ref.EvidenceID,
					URL: d.images.evidence[index][ref.EvidenceID], MediaID: record.ID, ModelSHA256: d.Manifest.Image.ModelSHA256}}}
		}
	}
	results := make([]VisualResult, 0, len(best))
	for _, result := range best {
		results = append(results, result)
	}
	sortVisual(results)
	var textByID map[string]Result
	var generated map[string]Match
	if len(textVector) > 0 {
		var text []Result
		var err error
		if observations {
			text, generated, err = d.observedText(textVector, query, true, filter)
		} else {
			text, err = d.search(textVector, query, true, filter, len(d.Entities), "")
		}
		if err != nil {
			return nil, err
		}
		combined := map[string]VisualResult{}
		textByID = map[string]Result{}
		for i, result := range results {
			result.Score = 1 / float64(60+i+1)
			combined[result.ID] = result
		}
		for i, result := range text {
			textByID[result.ID] = result
			visual := combined[result.ID]
			visual.Entity, visual.Cosine, visual.NameMatch = result.Entity, &result.Cosine, result.NameMatch
			visual.Ranking = result.Ranking
			visual.Snippet, visual.EvidenceID = result.Snippet, result.EvidenceID
			visual.Score += 1 / float64(60+i+1)
			visual.Matches = append(visual.Matches, Match{Channel: "text", Score: result.Score, EvidenceID: result.EvidenceID})
			combined[result.ID] = visual
		}
		results = make([]VisualResult, 0, len(combined))
		for _, result := range combined {
			results = append(results, result)
		}
		sortVisual(results)
	}
	if len(results) > limit {
		results = results[:limit]
	}
	for i := range results {
		if text, ok := textByID[results[i].ID]; ok {
			if text.Ranking != nil {
				matches, err := d.TextMatches(text)
				if err != nil {
					return nil, err
				}
				results[i].Matches = append(results[i].Matches[:len(results[i].Matches)-1], matches...)
				continue
			}
			match, derived := generated[text.ID]
			if !derived {
				var err error
				match, err = d.TextMatch(text)
				if err != nil {
					return nil, err
				}
			}
			results[i].Matches[len(results[i].Matches)-1] = match
		}
	}
	return results, nil
}

func sortVisual(results []VisualResult) {
	sort.Slice(results, func(i, j int) bool {
		if results[i].Score == results[j].Score {
			return results[i].ID < results[j].ID
		}
		return results[i].Score > results[j].Score
	})
}
