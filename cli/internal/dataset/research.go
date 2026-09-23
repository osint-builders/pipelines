package dataset

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"reflect"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"golang.org/x/text/cases"
	"golang.org/x/text/language"
	"golang.org/x/text/unicode/norm"
)

type ResearchField struct {
	Name string `json:"name"`
	Kind string `json:"kind"`
	Unit string `json:"unit"`
}

type ResearchManifest struct {
	Version   string          `json:"version"`
	Claims    int             `json:"claims"`
	Relations int             `json:"relations"`
	Fields    []ResearchField `json:"fields"`
}

type ResearchRawFact struct {
	Name      string    `json:"name"`
	Raw       string    `json:"raw"`
	Evidence  string    `json:"evidence"`
	Values    []float64 `json:"values"`
	Unit      *string   `json:"unit"`
	Qualifier *string   `json:"qualifier"`
}

type ResearchLocator struct {
	Kind  string `json:"kind"`
	Index int    `json:"index"`
}

type ResearchNumber struct {
	Min          *float64 `json:"min"`
	Max          *float64 `json:"max"`
	MinInclusive bool     `json:"min_inclusive"`
	MaxInclusive bool     `json:"max_inclusive"`
	Unit         string   `json:"unit"`
}

type ResearchDate struct {
	Min       string `json:"min"`
	Max       string `json:"max"`
	Precision string `json:"precision"`
}

type ResearchValue struct {
	Text   *string         `json:"text,omitempty"`
	Number *ResearchNumber `json:"number,omitempty"`
	Date   *ResearchDate   `json:"date,omitempty"`
}

type ResearchClaim struct {
	ID          string          `json:"id"`
	Entity      int             `json:"entity"`
	Field       string          `json:"field"`
	EvidenceID  string          `json:"evidence_id"`
	Locator     ResearchLocator `json:"locator"`
	Raw         ResearchRawFact `json:"raw"`
	Status      string          `json:"status"`
	Value       *ResearchValue  `json:"value"`
	EntityID    string          `json:"entity_id,omitempty"`
	EvidenceURL string          `json:"evidence_url,omitempty"`
}

type ResearchReference struct {
	Entity      int    `json:"entity"`
	EvidenceID  string `json:"evidence_id"`
	Quote       string `json:"quote"`
	EntityID    string `json:"entity_id,omitempty"`
	EvidenceURL string `json:"evidence_url,omitempty"`
}

type ResearchRelation struct {
	ID        string              `json:"id"`
	Source    int                 `json:"source"`
	Target    int                 `json:"target"`
	Type      string              `json:"type"`
	Basis     string              `json:"basis"`
	Rationale string              `json:"rationale"`
	Evidence  []ResearchReference `json:"evidence"`
	SourceID  string              `json:"source_id,omitempty"`
	TargetID  string              `json:"target_id,omitempty"`
}

type ResearchComparisonEntry struct {
	EntityID string          `json:"entity_id"`
	Status   string          `json:"status"`
	Claims   []ResearchClaim `json:"claims"`
}

type ResearchComparisonField struct {
	Field    string                    `json:"field"`
	Kind     string                    `json:"kind"`
	Unit     string                    `json:"unit"`
	Entities []ResearchComparisonEntry `json:"entities"`
}

type researchPage struct {
	ID           string `json:"id"`
	URL          string `json:"url"`
	CanonicalURL string `json:"canonical_url"`
	Markdown     string `json:"markdown"`
	RetrievedAt  string `json:"retrieved_at"`
}

type researchRecord struct {
	Facts    []json.RawMessage `json:"facts"`
	Evidence []researchPage    `json:"evidence"`
}

type researchData struct {
	claims    []ResearchClaim
	relations []ResearchRelation
	byEntity  map[int][]int
	records   map[int]*researchRecord
	resolved  map[int]bool
}

type researchFilter struct {
	dataset  *Dataset
	where    []string
	eligible map[string]bool
}

var researchID = regexp.MustCompile(`^(claim|relation):[a-f0-9]{24}$`)
var researchWhere = regexp.MustCompile(`^([a-z_]+)\s*(!=|<=|>=|=|<|>)\s*(.+)$`)
var researchNumeric = regexp.MustCompile(`^([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)\s*(.*)$`)
var researchDateLiteral = regexp.MustCompile(`^[0-9]{4}(?:-[0-9]{2}(?:-[0-9]{2})?)?$`)

func researchCatalog() []ResearchField {
	fields := []ResearchField{}
	for _, name := range []string{"manufacturer", "contractor", "origin_country", "designer_country", "operator_country", "site_country", "development_status"} {
		fields = append(fields, ResearchField{name, "text", ""})
	}
	for _, name := range []string{"service_entry", "first_flight", "launch_date", "retired", "publication_date", "updated_date", "captured_date"} {
		fields = append(fields, ResearchField{name, "date", ""})
	}
	for name, unit := range map[string]string{"range": "m", "detection_range": "m", "ferry_range": "m", "ceiling": "m", "length": "m", "height": "m", "width": "m", "wavelength": "m", "mass": "kg", "speed": "m/s", "frequency": "Hz", "pulse_repetition_frequency": "Hz", "power": "W", "crew": "count", "quantity": "count"} {
		fields = append(fields, ResearchField{name, "number", unit})
	}
	sort.Slice(fields, func(i, j int) bool { return fields[i].Name < fields[j].Name })
	return fields
}

func researchCatalogForVersion(version string) []ResearchField {
	fields := researchCatalog()
	switch version {
	case "entity-research-v1":
		return fields
	case "entity-research-v2":
		for _, name := range []string{"modulation", "reception_mode", "signal_location", "signal_status"} {
			fields = append(fields, ResearchField{name, "text", ""})
		}
		for _, name := range []string{"frequency_range", "bandwidth"} {
			fields = append(fields, ResearchField{name, "number", "Hz"})
		}
		sort.Slice(fields, func(i, j int) bool { return fields[i].Name < fields[j].Name })
		return fields
	default:
		return nil
	}
}

func (d *Dataset) HasResearch() bool { return d.Manifest.Research != nil }

func researchObject(raw []byte, names []string, nullable ...string) (map[string]json.RawMessage, error) {
	if !utf8.Valid(raw) {
		return nil, errors.New("invalid research UTF-8")
	}
	fields, err := rawObject(raw)
	if err != nil || !hasFields(fields, names...) {
		return nil, errors.New("invalid research object fields")
	}
	for name, value := range fields {
		if bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			allowed := false
			for _, key := range nullable {
				allowed = allowed || name == key
			}
			if !allowed {
				return nil, fmt.Errorf("null research field: %s", name)
			}
		}
	}
	return fields, nil
}

func (d *Dataset) validateResearchManifest(raw map[string]json.RawMessage) error {
	value, present := raw["research"]
	if !present {
		for name := range d.members {
			if strings.HasPrefix(name, "research/") {
				return errors.New("research member without research manifest")
			}
		}
		return nil
	}
	fields, err := researchObject(value, []string{"version", "claims", "relations", "fields"})
	if err != nil || d.Manifest.Research == nil {
		return errors.New("invalid research manifest")
	}
	m := d.Manifest.Research
	catalogFields := researchCatalogForVersion(m.Version)
	if catalogFields == nil || m.Claims < 0 || m.Claims > 100000 || m.Relations < 0 || m.Relations > 20000 || !reflect.DeepEqual(m.Fields, catalogFields) {
		return errors.New("unsupported research manifest or field catalog")
	}
	var catalog []json.RawMessage
	if err := json.Unmarshal(fields["fields"], &catalog); err != nil {
		return err
	}
	for _, field := range catalog {
		if _, err := researchObject(field, []string{"name", "kind", "unit"}); err != nil {
			return err
		}
	}
	for name := range d.members {
		if strings.HasPrefix(name, "research/") && name != "research/claims.json" && name != "research/relations.json" {
			return errors.New("unknown research member")
		}
	}
	for _, name := range []string{"research/claims.json", "research/relations.json"} {
		if d.members[name] == nil || !validDigest(d.Manifest.Files[name]) {
			return errors.New("missing research member or checksum")
		}
	}
	return nil
}

func decodeResearchRows(raw []byte) ([]json.RawMessage, error) {
	if !utf8.Valid(raw) {
		return nil, errors.New("invalid research UTF-8")
	}
	var rows []json.RawMessage
	if err := json.Unmarshal(raw, &rows); err != nil {
		return nil, err
	}
	if rows == nil {
		return nil, errors.New("research rows must be an array")
	}
	return rows, nil
}

func (d *Dataset) loadResearch() error {
	if d.research != nil {
		return nil
	}
	if !d.HasResearch() {
		return errors.New("this dataset has no research fields or relationships")
	}
	r := &researchData{claims: []ResearchClaim{}, relations: []ResearchRelation{}, byEntity: map[int][]int{}, records: map[int]*researchRecord{}, resolved: map[int]bool{}}
	catalog := map[string]ResearchField{}
	for _, field := range d.Manifest.Research.Fields {
		catalog[field.Name] = field
	}
	raw, err := d.readBounded("research/claims.json", 32<<20)
	if err != nil {
		return err
	}
	claims, err := decodeResearchRows(raw)
	if err != nil || len(claims) != d.Manifest.Research.Claims {
		return errors.New("invalid research claim count or rows")
	}
	previous := ""
	for _, raw := range claims {
		fields, err := researchObject(raw, []string{"id", "entity", "field", "evidence_id", "locator", "raw", "status", "value"}, "value")
		if err != nil {
			return err
		}
		if _, err := researchObject(fields["locator"], []string{"kind", "index"}); err != nil {
			return err
		}
		if _, err := researchObject(fields["raw"], []string{"name", "raw", "evidence", "values", "unit", "qualifier"}, "unit", "qualifier"); err != nil {
			return err
		}
		var claim ResearchClaim
		if err := json.Unmarshal(raw, &claim); err != nil {
			return err
		}
		field, exists := catalog[claim.Field]
		if !exists || !researchID.MatchString(claim.ID) || !strings.HasPrefix(claim.ID, "claim:") || claim.ID <= previous || claim.Entity < 0 || claim.Entity >= len(d.Entities) || !safeKey(claim.EvidenceID) ||
			(claim.Locator.Kind != "fact" && claim.Locator.Kind != "captured_at") || claim.Locator.Index < 0 || !boundedText(claim.Raw.Name, 1024) || len(claim.Raw.Raw) > 16384 || !validHTTPURL(claim.Raw.Evidence) || claim.Raw.Values == nil {
			return errors.New("invalid research claim")
		}
		if claim.Locator.Kind == "captured_at" && claim.Field != "captured_date" {
			return errors.New("capture locator requires captured_date")
		}
		previous = claim.ID
		if err := validateResearchValue(fields["value"], claim.Value, field, claim.Status); err != nil {
			return err
		}
		r.byEntity[claim.Entity] = append(r.byEntity[claim.Entity], len(r.claims))
		r.claims = append(r.claims, claim)
	}
	raw, err = d.readBounded("research/relations.json", 32<<20)
	if err != nil {
		return err
	}
	relations, err := decodeResearchRows(raw)
	if err != nil || len(relations) != d.Manifest.Research.Relations {
		return errors.New("invalid research relationship count or rows")
	}
	previous = ""
	pairs := map[string]bool{}
	for _, raw := range relations {
		fields, err := researchObject(raw, []string{"id", "source", "target", "type", "basis", "rationale", "evidence"})
		if err != nil {
			return err
		}
		var relation ResearchRelation
		if err := json.Unmarshal(raw, &relation); err != nil {
			return err
		}
		if !researchID.MatchString(relation.ID) || !strings.HasPrefix(relation.ID, "relation:") || relation.ID <= previous || !validRelationType(relation.Type) || relation.Basis != "reviewed_source_evidence" || !boundedText(relation.Rationale, 16384) ||
			relation.Source < 0 || relation.Source >= len(d.Entities) || relation.Target < 0 || relation.Target >= len(d.Entities) || relation.Source == relation.Target || len(relation.Evidence) < 2 || len(relation.Evidence) > 100 {
			return errors.New("invalid research relationship")
		}
		if (relation.Type == "equivalent" || relation.Type == "related_system") && relation.Source >= relation.Target {
			return errors.New("unordered symmetric relationship")
		}
		pair := fmt.Sprintf("%s:%d:%d", relation.Type, relation.Source, relation.Target)
		if pairs[pair] {
			return errors.New("duplicate research relationship")
		}
		pairs[pair], previous = true, relation.ID
		refs, err := decodeResearchRows(fields["evidence"])
		if err != nil {
			return err
		}
		endpoints := map[int]bool{}
		seen := map[string]bool{}
		for i, ref := range relation.Evidence {
			if _, err := researchObject(refs[i], []string{"entity", "evidence_id", "quote"}); err != nil {
				return err
			}
			identity := fmt.Sprintf("%d\x00%s\x00%s", ref.Entity, ref.EvidenceID, ref.Quote)
			if (ref.Entity != relation.Source && ref.Entity != relation.Target) || !safeKey(ref.EvidenceID) || !boundedText(ref.Quote, 16384) || seen[identity] {
				return errors.New("invalid relationship evidence")
			}
			seen[identity], endpoints[ref.Entity] = true, true
		}
		if !endpoints[relation.Source] || !endpoints[relation.Target] {
			return errors.New("relationship evidence must support both endpoints")
		}
		r.relations = append(r.relations, relation)
	}
	d.research = r
	return nil
}

func validateResearchValue(raw []byte, value *ResearchValue, field ResearchField, status string) error {
	if status != "known" && status != "unknown" && status != "unparsed" && status != "approximate" {
		return errors.New("invalid research claim status")
	}
	if value == nil {
		if status == "known" {
			return errors.New("known research claim requires a value")
		}
		return nil
	}
	if status == "unknown" || status == "unparsed" {
		return errors.New("unknown research claim cannot assert a value")
	}
	fields, err := researchObject(raw, []string{field.Kind})
	if err != nil {
		return err
	}
	switch field.Kind {
	case "text":
		if value.Text == nil || !boundedText(*value.Text, 16384) {
			return errors.New("invalid research text")
		}
	case "number":
		if _, err := researchObject(fields["number"], []string{"min", "max", "min_inclusive", "max_inclusive", "unit"}, "min", "max"); err != nil {
			return err
		}
		n := value.Number
		if n == nil || n.Unit != field.Unit || n.Min == nil && n.Max == nil || n.Min == nil && n.MinInclusive || n.Max == nil && n.MaxInclusive {
			return errors.New("invalid numeric research interval")
		}
		for _, bound := range []*float64{n.Min, n.Max} {
			if bound != nil && (math.IsNaN(*bound) || math.IsInf(*bound, 0) || *bound < 0 || field.Unit == "count" && *bound != math.Trunc(*bound)) {
				return errors.New("invalid numeric research bound")
			}
		}
		if n.Min != nil && n.Max != nil && (*n.Min > *n.Max || *n.Min == *n.Max && (!n.MinInclusive || !n.MaxInclusive)) {
			return errors.New("empty numeric research interval")
		}
	case "date":
		if _, err := researchObject(fields["date"], []string{"min", "max", "precision"}); err != nil {
			return err
		}
		if value.Date == nil {
			return errors.New("invalid research date")
		}
		date := value.Date
		length := map[string]int{"year": 4, "month": 7, "day": 10}[date.Precision]
		if length == 0 || len(date.Min) != 10 || len(date.Max) != 10 {
			return errors.New("invalid research date precision")
		}
		expected, err := parseResearchDate(date.Min[:length])
		if err != nil || *date != expected {
			return errors.New("invalid research date interval")
		}
	}
	return nil
}

func (d *Dataset) researchRecord(index int) (*researchRecord, error) {
	if record := d.research.records[index]; record != nil {
		return record, nil
	}
	raw, err := d.Export(d.Entities[index].ID, "json", "")
	if err != nil {
		return nil, err
	}
	var record researchRecord
	if err := json.Unmarshal(raw, &record); err != nil {
		return nil, err
	}
	for _, page := range record.Evidence {
		if !validHTTPURL(page.URL) || page.CanonicalURL != "" && !validHTTPURL(page.CanonicalURL) {
			return nil, errors.New("invalid research evidence URL")
		}
	}
	d.research.records[index] = &record
	return &record, nil
}

func researchURL(value string) string { return strings.SplitN(value, "#", 2)[0] }

func (d *Dataset) resolveResearchClaim(index int) (ResearchClaim, error) {
	claim := d.research.claims[index]
	if d.research.resolved[index] {
		return claim, nil
	}
	record, err := d.researchRecord(claim.Entity)
	if err != nil {
		return ResearchClaim{}, err
	}
	var expected ResearchRawFact
	switch claim.Locator.Kind {
	case "fact":
		if claim.Locator.Index >= len(record.Facts) {
			return ResearchClaim{}, errors.New("research fact locator is out of range")
		}
		raw := record.Facts[claim.Locator.Index]
		if _, err := researchObject(raw, []string{"name", "raw", "evidence", "values", "unit", "qualifier"}, "unit", "qualifier"); err != nil {
			return ResearchClaim{}, err
		}
		if err := json.Unmarshal(raw, &expected); err != nil {
			return ResearchClaim{}, err
		}
	case "captured_at":
		if claim.Locator.Index >= len(record.Evidence) {
			return ResearchClaim{}, errors.New("research capture locator is out of range")
		}
		page := record.Evidence[claim.Locator.Index]
		if page.ID != claim.EvidenceID {
			return ResearchClaim{}, errors.New("capture locator references unrelated evidence")
		}
		expected = ResearchRawFact{Name: "Captured at", Raw: page.RetrievedAt, Evidence: page.URL, Values: []float64{}}
	}
	if !reflect.DeepEqual(claim.Raw, expected) {
		return ResearchClaim{}, errors.New("research raw fact differs from captured source")
	}
	for _, page := range record.Evidence {
		if page.ID != claim.EvidenceID {
			continue
		}
		url := researchURL(claim.Raw.Evidence)
		if url != researchURL(page.URL) && (page.CanonicalURL == "" || url != researchURL(page.CanonicalURL)) {
			return ResearchClaim{}, errors.New("research fact references unrelated evidence")
		}
		claim.EntityID, claim.EvidenceURL = d.Entities[claim.Entity].ID, page.URL
		d.research.claims[index], d.research.resolved[index] = claim, true
		return claim, nil
	}
	return ResearchClaim{}, errors.New("research claim evidence does not belong to entity")
}

func validRelationType(kind string) bool {
	switch kind {
	case "equivalent", "related_system", "variant_of", "family_member_of", "component_of":
		return true
	}
	return false
}

func (d *Dataset) resolveResearchRelation(relation ResearchRelation) (ResearchRelation, error) {
	relation.Evidence = append([]ResearchReference(nil), relation.Evidence...)
	relation.SourceID, relation.TargetID = d.Entities[relation.Source].ID, d.Entities[relation.Target].ID
	for i, ref := range relation.Evidence {
		record, err := d.researchRecord(ref.Entity)
		if err != nil {
			return ResearchRelation{}, err
		}
		found := false
		for _, page := range record.Evidence {
			if page.ID == ref.EvidenceID && strings.Contains(page.Markdown, ref.Quote) {
				relation.Evidence[i].EntityID, relation.Evidence[i].EvidenceURL = d.Entities[ref.Entity].ID, page.URL
				found = true
				break
			}
		}
		if !found {
			return ResearchRelation{}, errors.New("relationship quote lacks captured entity evidence")
		}
	}
	return relation, nil
}

func (d *Dataset) verifyResearch() error {
	if !d.HasResearch() {
		return nil
	}
	if err := d.loadResearch(); err != nil {
		return err
	}
	for i := range d.research.claims {
		if _, err := d.resolveResearchClaim(i); err != nil {
			return err
		}
	}
	for _, relation := range d.research.relations {
		if _, err := d.resolveResearchRelation(relation); err != nil {
			return err
		}
	}
	return nil
}

func (d *Dataset) ResearchFacts(id string) ([]ResearchClaim, error) {
	entity, ok := d.byID[id]
	if !ok {
		return nil, fmt.Errorf("unknown entity ID: %s", id)
	}
	if err := d.loadResearch(); err != nil {
		return nil, err
	}
	claims := []ResearchClaim{}
	for _, i := range d.research.byEntity[entity] {
		claim, err := d.resolveResearchClaim(i)
		if err != nil {
			return nil, err
		}
		claims = append(claims, claim)
	}
	return claims, nil
}

func (d *Dataset) Relationships(id, relationType string) ([]ResearchRelation, error) {
	entity, ok := d.byID[id]
	if !ok {
		return nil, fmt.Errorf("unknown entity ID: %s", id)
	}
	if relationType != "" && !validRelationType(relationType) {
		return nil, fmt.Errorf("unknown relationship type: %s", relationType)
	}
	if err := d.loadResearch(); err != nil {
		return nil, err
	}
	relations := []ResearchRelation{}
	for _, relation := range d.research.relations {
		if (relation.Source != entity && relation.Target != entity) || relationType != "" && relation.Type != relationType {
			continue
		}
		resolved, err := d.resolveResearchRelation(relation)
		if err != nil {
			return nil, err
		}
		relations = append(relations, resolved)
	}
	return relations, nil
}

func (d *Dataset) Compare(ids []string) ([]ResearchComparisonField, error) {
	if len(ids) < 2 || len(ids) > 20 {
		return nil, errors.New("compare requires between 2 and 20 unique entity IDs")
	}
	seen := map[string]bool{}
	claims := map[string][]ResearchClaim{}
	for _, id := range ids {
		if seen[id] {
			return nil, errors.New("compare requires unique entity IDs")
		}
		seen[id] = true
		rows, err := d.ResearchFacts(id)
		if err != nil {
			return nil, err
		}
		claims[id] = rows
	}
	comparison := []ResearchComparisonField{}
	for _, field := range d.Manifest.Research.Fields {
		row := ResearchComparisonField{Field: field.Name, Kind: field.Kind, Unit: field.Unit, Entities: []ResearchComparisonEntry{}}
		for _, id := range ids {
			entry := ResearchComparisonEntry{EntityID: id, Status: "unknown", Claims: []ResearchClaim{}}
			for _, claim := range claims[id] {
				if claim.Field == field.Name {
					entry.Claims = append(entry.Claims, claim)
					entry.Status = "claims"
				}
			}
			row.Entities = append(row.Entities, entry)
		}
		comparison = append(comparison, row)
	}
	return comparison, nil
}

func (d *Dataset) List(filter Filter, limit int) ([]Entity, int, error) {
	if limit < 1 || limit > 100 {
		return nil, 0, errors.New("limit must be between 1 and 100")
	}
	filter, err := d.PrepareFilter(filter)
	if err != nil {
		return nil, 0, err
	}
	entities := []Entity{}
	for _, entity := range d.Entities {
		if filter.matches(entity) {
			entities = append(entities, entity)
		}
	}
	sort.Slice(entities, func(i, j int) bool { return entities[i].ID < entities[j].ID })
	total := len(entities)
	if total > limit {
		entities = entities[:limit]
	}
	return entities, total, nil
}

type researchPredicate struct {
	field ResearchField
	op    string
	text  string
	num   float64
	date  ResearchDate
}

func normalizeResearchText(value string) string {
	return strings.Join(strings.Fields(cases.Lower(language.Und).String(norm.NFKC.String(value))), " ")
}

func parseResearchDate(value string) (ResearchDate, error) {
	if !researchDateLiteral.MatchString(value) {
		return ResearchDate{}, errors.New("date must be YYYY, YYYY-MM, or YYYY-MM-DD")
	}
	layout, precision := "2006", "year"
	if len(value) == 7 {
		layout, precision = "2006-01", "month"
	} else if len(value) == 10 {
		layout, precision = "2006-01-02", "day"
	}
	start, err := time.Parse(layout, value)
	if err != nil || start.Year() < 1 || start.Year() > 9999 {
		return ResearchDate{}, errors.New("invalid date literal")
	}
	end := start
	if precision == "year" {
		end = start.AddDate(1, 0, -1)
	} else if precision == "month" {
		end = start.AddDate(0, 1, -1)
	}
	return ResearchDate{Min: start.Format("2006-01-02"), Max: end.Format("2006-01-02"), Precision: precision}, nil
}

type researchUnit struct {
	canonical string
	factor    float64
}

func parseResearchUnit(name string) (researchUnit, bool) {
	symbol := strings.Join(strings.Fields(norm.NFKC.String(name)), " ")
	si := map[string]researchUnit{
		"m": {"m", 1}, "km": {"m", 1000}, "cm": {"m", .01}, "mm": {"m", .001},
		"kg": {"kg", 1}, "g": {"kg", .001}, "t": {"kg", 1000},
		"m/s": {"m/s", 1}, "km/h": {"m/s", 1.0 / 3.6},
		"Hz": {"Hz", 1}, "mHz": {"Hz", .001}, "kHz": {"Hz", 1000}, "MHz": {"Hz", 1000000}, "GHz": {"Hz", 1000000000},
		"W": {"W", 1}, "mW": {"W", .001}, "kW": {"W", 1000}, "MW": {"W", 1000000},
	}
	if unit, ok := si[symbol]; ok {
		return unit, true
	}
	units := map[string]researchUnit{}
	add := func(canonical string, factor float64, names ...string) {
		for _, name := range names {
			units[name] = researchUnit{canonical, factor}
		}
	}
	add("m", 1, "metre", "meter", "metres", "meters")
	add("m", 1000, "kilometre", "kilometer", "kilometres", "kilometers")
	add("m", .01, "centimeter", "centimeters", "centimetre", "centimetres")
	add("m", .001, "millimeter", "millimeters", "millimetre", "millimetres")
	add("m", .3048, "ft", "foot", "feet")
	add("m", .0254, "in", "inch", "inches")
	add("m", 1609.344, "mi", "mile", "miles")
	add("m", 1852, "nmi")
	add("kg", 1, "kilogram", "kilograms")
	add("kg", .001, "gram", "grams")
	add("kg", 1000, "tonne", "tonnes", "metric ton", "metric tons")
	add("kg", .45359237, "lb", "lbs", "pound", "pounds")
	add("m/s", 1.0/3.6, "kph")
	add("m/s", .44704, "mph")
	add("m/s", 1852.0/3600, "kn", "knot", "knots")
	add("count", 1, "count", "")
	unit, ok := units[normalizeResearchText(name)]
	return unit, ok
}

func parseResearchPredicate(expression string, catalog map[string]ResearchField) (researchPredicate, error) {
	if len(expression) > 4096 || !utf8.ValidString(expression) || strings.ContainsRune(expression, 0) {
		return researchPredicate{}, errors.New("invalid or oversized research predicate")
	}
	parts := researchWhere.FindStringSubmatch(strings.TrimSpace(expression))
	if parts == nil || strings.TrimSpace(parts[3]) == "" {
		return researchPredicate{}, errors.New("predicate must be FIELD OP VALUE, with = != < <= > or >=")
	}
	field, ok := catalog[parts[1]]
	if !ok {
		return researchPredicate{}, fmt.Errorf("unknown research field: %s", parts[1])
	}
	p := researchPredicate{field: field, op: parts[2]}
	value := strings.TrimSpace(parts[3])
	switch field.Kind {
	case "text":
		if p.op != "=" && p.op != "!=" {
			return p, errors.New("text fields support only = and !=")
		}
		p.text = normalizeResearchText(value)
	case "date":
		date, err := parseResearchDate(value)
		if err != nil {
			return p, err
		}
		p.date = date
	case "number":
		parts := researchNumeric.FindStringSubmatch(value)
		if parts == nil {
			return p, errors.New("numeric predicate requires one finite number and compatible unit")
		}
		number, err := strconv.ParseFloat(parts[1], 64)
		unit, ok := parseResearchUnit(parts[2])
		if err != nil || !ok || unit.canonical != field.Unit || math.IsNaN(number) || math.IsInf(number, 0) {
			return p, errors.New("numeric predicate requires one finite number and compatible unit")
		}
		p.num = number * unit.factor
		if math.IsNaN(p.num) || math.IsInf(p.num, 0) {
			return p, errors.New("numeric predicate overflows its canonical unit")
		}
	}
	return p, nil
}

func researchNumberCompare(a, b float64) int {
	if math.Abs(a-b) <= 1e-9*math.Max(1, math.Max(math.Abs(a), math.Abs(b))) {
		return 0
	}
	if a < b {
		return -1
	}
	return 1
}

func (p researchPredicate) matches(claim ResearchClaim) bool {
	if claim.Status != "known" || claim.Value == nil {
		return false
	}
	switch p.field.Kind {
	case "text":
		equal := normalizeResearchText(*claim.Value.Text) == p.text
		return p.op == "=" && equal || p.op == "!=" && !equal
	case "date":
		v := claim.Value.Date
		switch p.op {
		case "=":
			return v.Min == p.date.Min && v.Max == p.date.Max && v.Precision == p.date.Precision
		case "!=":
			return v.Max < p.date.Min || v.Min > p.date.Max
		case "<":
			return v.Max < p.date.Min
		case "<=":
			return v.Max <= p.date.Max
		case ">":
			return v.Min > p.date.Max
		case ">=":
			return v.Min >= p.date.Min
		}
	case "number":
		v := claim.Value.Number
		lower, upper := -1, 1
		if v.Min != nil {
			lower = researchNumberCompare(*v.Min, p.num)
		}
		if v.Max != nil {
			upper = researchNumberCompare(*v.Max, p.num)
		}
		switch p.op {
		case "=":
			return v.Min != nil && v.Max != nil && *v.Min == *v.Max && lower == 0 && v.MinInclusive && v.MaxInclusive
		case "!=":
			return upper < 0 || upper == 0 && !v.MaxInclusive || lower > 0 || lower == 0 && !v.MinInclusive
		case "<":
			return upper < 0 || upper == 0 && !v.MaxInclusive
		case "<=":
			return upper <= 0
		case ">":
			return lower > 0 || lower == 0 && !v.MinInclusive
		case ">=":
			return lower >= 0
		}
	}
	return false
}

// PrepareFilter validates predicates once and fixes eligibility before any ranking.
func (d *Dataset) PrepareFilter(filter Filter) (Filter, error) {
	if len(filter.Where) == 0 {
		filter.prepared = nil
		return filter, nil
	}
	if len(filter.Where) > 32 {
		return Filter{}, errors.New("at most 32 research predicates are supported")
	}
	if filter.prepared != nil && filter.prepared.dataset == d && reflect.DeepEqual(filter.prepared.where, filter.Where) {
		return filter, nil
	}
	if !d.HasResearch() {
		return Filter{}, errors.New("this dataset has no research fields or relationships")
	}
	catalog := map[string]ResearchField{}
	for _, field := range d.Manifest.Research.Fields {
		catalog[field.Name] = field
	}
	groups := map[string][]researchPredicate{}
	for _, expression := range filter.Where {
		predicate, err := parseResearchPredicate(expression, catalog)
		if err != nil {
			return Filter{}, err
		}
		groups[predicate.field.Name] = append(groups[predicate.field.Name], predicate)
	}
	if err := d.loadResearch(); err != nil {
		return Filter{}, err
	}
	prepared := &researchFilter{dataset: d, where: append([]string(nil), filter.Where...), eligible: map[string]bool{}}
	for entity, rows := range d.research.byEntity {
		winning := map[string]int{}
		for _, row := range rows {
			claim := d.research.claims[row]
			predicates := groups[claim.Field]
			if _, found := winning[claim.Field]; found || len(predicates) == 0 {
				continue
			}
			all := true
			for _, predicate := range predicates {
				all = all && predicate.matches(claim)
			}
			if all {
				winning[claim.Field] = row
			}
		}
		if len(winning) != len(groups) {
			continue
		}
		for _, row := range winning {
			if _, err := d.resolveResearchClaim(row); err != nil {
				return Filter{}, err
			}
		}
		prepared.eligible[d.Entities[entity].ID] = true
	}
	filter.prepared = prepared
	return filter, nil
}
