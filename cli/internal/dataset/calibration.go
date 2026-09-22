package dataset

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"math"
	"sort"
	"strconv"
	"strings"
)

const calibrationProtocol = "ccf9e230ab553430ebacc320fed8413ba7e137a459fdfd8fe8e470a0e1e69849"

type CalibrationManifest struct {
	SchemaVersion   int    `json:"schema_version"`
	Member          string `json:"member"`
	SHA256          string `json:"sha256"`
	Profiles        int    `json:"profiles"`
	RetrievalSHA256 string `json:"retrieval_sha256"`
}

type calibrationContext struct {
	QueryType              string  `json:"query_type"`
	TextMode               *string `json:"text_mode"`
	Observations           bool    `json:"observations"`
	EligibleEntitiesSHA256 string  `json:"eligible_entities_sha256"`
}

type calibrationProfile struct {
	calibrationContext
	Minimums map[string]int `json:"minimums"`
}

type calibrationArtifact struct {
	SchemaVersion   int                  `json:"schema_version"`
	FeatureVersion  string               `json:"feature_version"`
	FitVersion      string               `json:"fit_version"`
	ProtocolSHA256  string               `json:"protocol_sha256"`
	RetrievalSHA256 string               `json:"retrieval_sha256"`
	Development     map[string]string    `json:"development"`
	Profiles        []calibrationProfile `json:"profiles"`
	sha256          string
}

type CalibrationCandidate struct {
	Score   float64  `json:"score"`
	Cosine  *float64 `json:"cosine"`
	Matches []Match  `json:"matches"`
}

type CalibrationDecision struct {
	ArtifactSHA256 string         `json:"artifact_sha256"`
	ProfileID      string         `json:"profile_id"`
	Reason         string         `json:"reason"`
	Features       map[string]int `json:"features"`
	Minimums       map[string]int `json:"minimums"`
}

type CalibrationOverride struct {
	MatchStatus       string              `json:"match_status"`
	CalibrationStatus string              `json:"calibration_status"`
	Decision          CalibrationDecision `json:"decision"`
}

func calibrationFrame(value any) ([]byte, error) {
	var result bytes.Buffer
	var appendValue func(any) error
	appendValue = func(value any) error {
		switch value := value.(type) {
		case nil:
			result.WriteByte('n')
		case bool:
			if value {
				result.WriteByte('t')
			} else {
				result.WriteByte('f')
			}
		case float64:
			if math.IsNaN(value) || math.IsInf(value, 0) || math.Abs(value) > 9007199254740991 {
				return errors.New("invalid calibration binding number")
			}
			if value == 0 {
				value = 0
			}
			fmt.Fprintf(&result, "d%016x;", math.Float64bits(value))
		case string:
			fmt.Fprintf(&result, "s%d:", len(value))
			result.WriteString(value)
		case []any:
			fmt.Fprintf(&result, "a%d:", len(value))
			for _, item := range value {
				if err := appendValue(item); err != nil {
					return err
				}
			}
		case map[string]any:
			keys := make([]string, 0, len(value))
			for key := range value {
				keys = append(keys, key)
			}
			sort.Strings(keys)
			fmt.Fprintf(&result, "o%d:", len(keys))
			for _, key := range keys {
				if err := appendValue(key); err != nil {
					return err
				}
				if err := appendValue(value[key]); err != nil {
					return err
				}
			}
		default:
			return errors.New("invalid calibration binding value")
		}
		return nil
	}
	if err := appendValue(value); err != nil {
		return nil, err
	}
	return result.Bytes(), nil
}

func calibrationRetrieval(manifest map[string]any) (string, error) {
	value := make(map[string]any, len(manifest))
	for key, item := range manifest {
		if key != "dataset_id" && key != "recipe_sha256" && key != "calibration" {
			value[key] = item
		}
	}
	files, ok := manifest["files"].(map[string]any)
	if !ok {
		return "", errors.New("invalid calibration manifest files")
	}
	boundFiles := make(map[string]any, len(files))
	for key, item := range files {
		if key != "calibration.json" {
			boundFiles[key] = item
		}
	}
	value["files"] = boundFiles
	if value["format_version"] == float64(5) {
		value["format_version"] = float64(4)
	}
	body, err := calibrationFrame(value)
	if err != nil {
		return "", err
	}
	return digestString(append([]byte("calibration-retrieval-v1\n"), body...)), nil
}

func calibrationScope(ids []string) (string, error) {
	unique := map[string]bool{}
	for _, id := range ids {
		source, key, found := strings.Cut(id, ":")
		if !found || source == "" || key == "" {
			return "", errors.New("calibration scope requires source-qualified IDs")
		}
		unique[id] = true
	}
	keys := make([]string, 0, len(unique))
	for id := range unique {
		keys = append(keys, id)
	}
	sort.Strings(keys)
	values := make([]any, len(keys))
	for i, id := range keys {
		values[i] = id
	}
	body, err := calibrationFrame(values)
	if err != nil {
		return "", err
	}
	return digestString(append([]byte("calibration-scope-v1\n"), body...)), nil
}

func (c calibrationContext) identity() (string, error) {
	mode := ""
	if c.TextMode != nil {
		mode = *c.TextMode
	}
	valid := c.QueryType == "text" && (mode == "hybrid" || mode == "vector") ||
		c.QueryType == "image_text" && mode == "hybrid" ||
		c.QueryType == "image" && c.TextMode == nil && !c.Observations
	if !valid || !validDigest(c.EligibleEntitiesSHA256) {
		return "", errors.New("invalid calibration profile context")
	}
	body, err := json.Marshal(map[string]any{"query_type": c.QueryType, "text_mode": c.TextMode,
		"observations": c.Observations, "eligible_entities_sha256": c.EligibleEntitiesSHA256})
	if err != nil {
		return "", err
	}
	return digestString(body), nil
}

func calibrationFeatureNames(kind string) []string {
	switch kind {
	case "text":
		return []string{"semantic_cosine", "ranking_margin"}
	case "image":
		return []string{"image_cosine", "ranking_margin"}
	case "image_text":
		return []string{"image_cosine", "semantic_cosine", "ranking_margin"}
	}
	return nil
}

func parseCalibration(body []byte, retrieval string) (*calibrationArtifact, error) {
	if len(body) > 65536 {
		return nil, errors.New("calibration member exceeds size limit")
	}
	fields, err := rawObject(body)
	if err != nil || !hasFields(fields, "schema_version", "feature_version", "fit_version", "protocol_sha256", "retrieval_sha256", "development", "profiles") {
		return nil, errors.New("invalid calibration artifact fields")
	}
	var canonical map[string]any
	if err := json.Unmarshal(body, &canonical); err != nil {
		return nil, err
	}
	canonicalBody, err := json.Marshal(canonical)
	if err != nil || !bytes.Equal(canonicalBody, body) {
		return nil, errors.New("calibration member must use canonical JSON")
	}
	var artifact calibrationArtifact
	if err := json.Unmarshal(body, &artifact); err != nil {
		return nil, err
	}
	if artifact.SchemaVersion != 1 || artifact.FeatureVersion != "candidate-signals-v1" || artifact.FitVersion != "bounded-threshold-grid-v1" || artifact.ProtocolSHA256 != calibrationProtocol || artifact.RetrievalSHA256 != retrieval {
		return nil, errors.New("calibration version or retrieval binding mismatch")
	}
	if len(artifact.Development) != 4 {
		return nil, errors.New("invalid calibration development identities")
	}
	for _, key := range []string{"fixture_sha256", "responses_sha256", "binary_sha256", "contract_sha256"} {
		if !validDigest(artifact.Development[key]) {
			return nil, errors.New("invalid calibration development identity")
		}
	}
	if artifact.Development["contract_sha256"] != "3460166733affac363e071d5b8fbb7f8ae267d7c2ff323a033ae2356a83c63f9" {
		return nil, errors.New("calibration contract identity mismatch")
	}
	if len(artifact.Profiles) < 1 || len(artifact.Profiles) > 256 {
		return nil, errors.New("invalid calibration profile count")
	}
	var profiles []json.RawMessage
	if err := json.Unmarshal(fields["profiles"], &profiles); err != nil {
		return nil, err
	}
	previous := ""
	for i, profile := range artifact.Profiles {
		fields, err := rawObject(profiles[i])
		if err != nil || !hasFields(fields, "query_type", "text_mode", "observations", "eligible_entities_sha256", "minimums") ||
			(string(fields["observations"]) != "true" && string(fields["observations"]) != "false") {
			return nil, errors.New("invalid calibration profile fields")
		}
		identity, err := profile.identity()
		if err != nil {
			return nil, err
		}
		if identity <= previous {
			return nil, errors.New("calibration profiles must be unique and sorted by ID")
		}
		previous = identity
		features := calibrationFeatureNames(profile.QueryType)
		if len(profile.Minimums) != len(features) {
			return nil, errors.New("invalid calibration feature thresholds")
		}
		thresholds, err := rawObject(fields["minimums"])
		if err != nil {
			return nil, err
		}
		for _, name := range features {
			value, exists := profile.Minimums[name]
			lower, upper := -1000000, 1000001
			if name == "ranking_margin" {
				lower, upper = 0, 4000001
			}
			if !exists || value < lower || value > upper || string(thresholds[name]) != strconv.Itoa(value) {
				return nil, errors.New("invalid calibration threshold value")
			}
		}
	}
	artifact.sha256 = digestString(body)
	return &artifact, nil
}

func (d *Dataset) loadCalibration(fields map[string]json.RawMessage) error {
	_, declared := fields["calibration"]
	_, listed := d.Manifest.Files["calibration.json"]
	if d.Manifest.FormatVersion != 5 {
		if declared || listed || d.members["calibration.json"] != nil {
			return errors.New("calibration requires dataset format 5")
		}
		return nil
	}
	if !declared || d.Manifest.Calibration == nil {
		return errors.New("dataset format 5 requires calibration")
	}
	descriptor, err := rawObject(fields["calibration"])
	if err != nil || !hasFields(descriptor, "schema_version", "member", "sha256", "profiles", "retrieval_sha256") {
		return errors.New("invalid calibration descriptor fields")
	}
	meta := d.Manifest.Calibration
	if meta.SchemaVersion != 1 || meta.Member != "calibration.json" || !validDigest(meta.SHA256) || !validDigest(meta.RetrievalSHA256) || meta.Profiles < 1 || meta.Profiles > 256 || d.Manifest.Files[meta.Member] != meta.SHA256 {
		return errors.New("invalid calibration descriptor")
	}
	raw, err := json.Marshal(fields)
	if err != nil {
		return err
	}
	var manifest map[string]any
	if err := json.Unmarshal(raw, &manifest); err != nil {
		return err
	}
	retrieval, err := calibrationRetrieval(manifest)
	if err != nil {
		return err
	}
	if meta.RetrievalSHA256 != retrieval {
		return errors.New("calibration descriptor retrieval mismatch")
	}
	body, err := d.readImage(meta.Member, 65536)
	if err != nil {
		return err
	}
	artifact, err := parseCalibration(body, retrieval)
	if err != nil {
		return err
	}
	if len(artifact.Profiles) != meta.Profiles {
		return errors.New("calibration descriptor profile count mismatch")
	}
	d.calibration = artifact
	return nil
}

func (d *Dataset) verifyCalibration() error {
	body, err := fs.ReadFile(d.Files, "manifest.json")
	if err != nil {
		return err
	}
	fields, err := rawObject(body)
	if err != nil {
		return err
	}
	return d.loadCalibration(fields)
}

func (d *Dataset) HasCalibration() bool { return d.calibration != nil }

func candidateSignals(kind string, results []CalibrationCandidate) (map[string]int, error) {
	if len(results) == 0 {
		return nil, nil
	}
	finite := func(v float64) bool { return !math.IsNaN(v) && !math.IsInf(v, 0) }
	first := results[0]
	if !finite(first.Score) {
		return nil, errors.New("calibration requires finite scores")
	}
	margin := 0.0
	if len(results) > 1 {
		if !finite(results[1].Score) {
			return nil, errors.New("calibration requires finite scores")
		}
		margin = first.Score - results[1].Score
	}
	if margin < -1e-12 || margin > 4 {
		return nil, errors.New("calibration response ranking is not ordered")
	}
	quantize := func(v float64) int { return int(math.Round(v * 1000000)) }
	cosine := func(v float64) int { return quantize(math.Max(-1, math.Min(1, v))) }
	features := map[string]int{"ranking_margin": quantize(math.Max(0, margin))}
	if kind == "text" || kind == "image_text" {
		if first.Cosine == nil {
			return nil, nil
		}
		if !finite(*first.Cosine) {
			return nil, errors.New("calibration requires finite cosine")
		}
		features["semantic_cosine"] = cosine(*first.Cosine)
	}
	if kind == "image" || kind == "image_text" {
		best, found := -math.MaxFloat64, false
		for _, match := range first.Matches {
			if match.Channel == "image" {
				if !finite(match.Score) {
					return nil, errors.New("calibration requires finite image scores")
				}
				best, found = math.Max(best, match.Score), true
			}
		}
		if !found {
			return nil, nil
		}
		features["image_cosine"] = cosine(best)
	}
	return features, nil
}

func (a *calibrationArtifact) decide(context calibrationContext, results []CalibrationCandidate) (*CalibrationOverride, error) {
	identity, err := context.identity()
	if err != nil {
		return nil, err
	}
	for _, profile := range a.Profiles {
		profileID, _ := profile.identity()
		if profileID != identity {
			continue
		}
		features, err := candidateSignals(context.QueryType, results)
		if err != nil {
			return nil, err
		}
		reason, status := "accepted", "candidates"
		if features == nil {
			reason = "missing_features"
			if len(results) == 0 {
				reason = "no_results"
			}
		} else {
			for name, threshold := range profile.Minimums {
				if features[name] < threshold {
					reason = "below_threshold"
				}
			}
		}
		if reason != "accepted" {
			status = "no_supported_match"
		}
		return &CalibrationOverride{status, "calibrated", CalibrationDecision{a.sha256, identity, reason, features, profile.Minimums}}, nil
	}
	return nil, nil
}

// Calibrate applies an optional rule for the exact mode and eligible entity pool.
// Results must contain the first two candidates, or the entire smaller pool.
func (d *Dataset) Calibrate(queryType, textMode string, observations bool, filter Filter, results []CalibrationCandidate) (*CalibrationOverride, error) {
	if !d.HasCalibration() {
		return nil, nil
	}
	filter, err := d.PrepareFilter(filter)
	if err != nil {
		return nil, err
	}
	var ids []string
	if queryType == "image" {
		if err := d.LoadImages(); err != nil {
			return nil, err
		}
		for _, record := range d.images.records {
			if record.VectorIndex == nil {
				continue
			}
			for _, reference := range record.References {
				entity := d.Entities[d.byID[reference.EntityID]]
				if filter.matches(entity) {
					ids = append(ids, entity.ID)
				}
			}
		}
	} else {
		for _, entity := range d.Entities {
			if filter.matches(entity) {
				ids = append(ids, entity.ID)
			}
		}
	}
	scope, err := calibrationScope(ids)
	if err != nil {
		return nil, err
	}
	context := calibrationContext{QueryType: queryType, Observations: observations, EligibleEntitiesSHA256: scope}
	if queryType != "image" {
		context.TextMode = &textMode
	}
	return d.calibration.decide(context, results)
}
