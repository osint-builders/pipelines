package dataset

import (
	"math"
	"regexp"
	"strconv"
	"strings"
)

const specificationNumber = `[+-]?(?:(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?`

var specificationValue = regexp.MustCompile(`^(` + specificationNumber + `)(?:\s*(?:[-–—]|to)\s*(` + specificationNumber + `))?\s*([^0-9]*)$`)

type specification struct {
	label, text, unit string
	min, max          float64
}

func specificationLabel(label string) string {
	// Source adapters retain section prefixes in raw fact names.
	if at := strings.LastIndexByte(label, ':'); at >= 0 {
		label = label[at+1:]
	}
	return strings.Join(lexicalTokens(label), " ")
}

func querySpecifications(query string) []specification {
	result := []specification{}
	seen := map[specification]bool{}
	for _, clause := range strings.FieldsFunc(query, func(r rune) bool { return r == ';' || r == '\n' }) {
		label, value, ok := strings.Cut(clause, ":")
		if !ok {
			continue
		}
		parts := specificationValue.FindStringSubmatch(strings.TrimSpace(value))
		if parts == nil {
			continue
		}
		unit, ok := parseResearchUnit(strings.TrimSpace(parts[3]))
		if !ok {
			continue
		}
		parse := func(s string) float64 {
			v, err := strconv.ParseFloat(strings.ReplaceAll(s, ",", ""), 64)
			if err != nil {
				return math.NaN()
			}
			return v * unit.factor
		}
		term := specification{label: specificationLabel(label), unit: unit.canonical, min: parse(parts[1])}
		term.max = term.min
		if parts[2] != "" {
			term.max = parse(parts[2])
		}
		if term.label == "" || math.IsNaN(term.min) || math.IsNaN(term.max) || math.IsInf(term.min, 0) || math.IsInf(term.max, 0) || term.min > term.max || seen[term] {
			continue
		}
		seen[term] = true
		term.text = strings.TrimSpace(clause)
		result = append(result, term)
	}
	return result
}

func (s specification) matches(claim ResearchClaim) bool {
	if claim.Status != "known" || claim.Value == nil || claim.Value.Number == nil ||
		(s.label != specificationLabel(claim.Raw.Name) && s.label != specificationLabel(claim.Field)) {
		return false
	}
	v := claim.Value.Number
	return s.unit == v.Unit && v.Min != nil && v.Max != nil && v.MinInclusive && v.MaxInclusive &&
		researchNumberCompare(s.min, *v.Min) == 0 && researchNumberCompare(s.max, *v.Max) == 0
}

// Exact source-backed specification coverage supplements lexical and semantic
// ranking. A value from a different property or unit never earns this signal.
func (d *Dataset) rankSpecifications(results []Result, positions map[int]int, query string) error {
	terms := querySpecifications(query)
	if len(terms) == 0 || !d.HasResearch() {
		return nil
	}
	if err := d.loadResearch(); err != nil {
		return err
	}
	for i := range results {
		results[i].Ranking.SpecificationTerms = len(terms)
	}
	for _, term := range terms {
		matched := map[int]bool{}
		for index, claim := range d.research.claims {
			position, eligible := positions[claim.Entity]
			if !eligible || matched[claim.Entity] || !term.matches(claim) {
				continue
			}
			resolved, err := d.resolveResearchClaim(index)
			if err != nil {
				return err
			}
			matched[claim.Entity] = true
			result := &results[position]
			weight := 1.0 / float64(len(terms))
			result.Score += weight
			result.matches = append(result.matches, Match{Channel: "text", Method: "specification", Reason: "source_fact", Score: weight,
				ClaimID: resolved.ID, EvidenceID: resolved.EvidenceID, URL: resolved.EvidenceURL, Terms: []string{term.text}})
			result.Snippet = resolved.Raw.Name + ": " + resolved.Raw.Raw
			result.EvidenceID = resolved.EvidenceID
		}
	}
	return nil
}
