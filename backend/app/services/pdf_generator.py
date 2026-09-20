import io
import html
import datetime
try:
    from weasyprint import HTML
    WEASYPRINT_AVAILABLE = True
except OSError:
    WEASYPRINT_AVAILABLE = False

class PDFGeneratorService:
    """
    Generates beautiful, production-grade PDF plagiarism analysis reports using WeasyPrint.
    Uses HTML templates and CSS Paged Media rules.
    """

    @staticmethod
    def _get_highlighted_html(text: str, sentences: list[dict], matches: list[dict]) -> str:
        """
        Reconstructs the original document text with word-level <mark> highlights
        based on plagiarism coordinate mappings.
        """
        # Map sentence start_char to its match for quick lookup
        matches_map = {m["query_sentence"]["start_char"]: m for m in matches}
        
        html_parts = []
        last_offset = 0
        
        for s in sentences:
            start = s["start_char"]
            end = s["end_char"]
            
            # Append any raw text between sentences (spaces, newlines, etc.)
            if start > last_offset:
                raw_between = text[last_offset:start]
                # Replace newlines with <br> to preserve line breaks
                html_parts.append(html.escape(raw_between).replace("\n", "<br>"))
                
            sent_text = s["text"]
            match = matches_map.get(start)
            
            if match:
                highlights = match.get("highlights", [])
                if highlights:
                    # Sort highlights by relative start offset in the sentence
                    sorted_hls = []
                    for hl in highlights:
                        rel_start = hl["start_char"] - start
                        rel_end = hl["end_char"] - start
                        # Make sure boundaries are within bounds
                        if 0 <= rel_start < rel_end <= len(sent_text):
                            sorted_hls.append((rel_start, rel_end, hl["text"]))
                            
                    sorted_hls.sort(key=lambda x: x[0])
                    
                    # Merge overlapping or touching highlights relative to sentence
                    merged_hls = []
                    if sorted_hls:
                        merged_hls.append(sorted_hls[0])
                        for curr in sorted_hls[1:]:
                            prev = merged_hls[-1]
                            if curr[0] <= prev[1]:
                                merged_hls[-1] = (prev[0], max(prev[1], curr[1]), sent_text[prev[0]:max(prev[1], curr[1])])
                            else:
                                merged_hls.append(curr)
                                
                    sent_html = []
                    last_sent_idx = 0
                    
                    for rel_start, rel_end, hl_text in merged_hls:
                        if rel_start > last_sent_idx:
                            sent_html.append(html.escape(sent_text[last_sent_idx:rel_start]))
                        
                        if match["match_type"] == "lexical":
                            mark_class = "mark-lexical"
                        elif match["match_type"] == "hybrid":
                            mark_class = "mark-hybrid"
                        else:
                            mark_class = "mark-semantic"
                        sent_html.append(f'<mark class="{mark_class}">{html.escape(sent_text[rel_start:rel_end])}</mark>')
                        last_sent_idx = rel_end
                        
                    if last_sent_idx < len(sent_text):
                        sent_html.append(html.escape(sent_text[last_sent_idx:]))
                    
                    sentence_html_content = "".join(sent_html)
                else:
                    if match["match_type"] == "lexical":
                        mark_class = "mark-lexical"
                    elif match["match_type"] == "hybrid":
                        mark_class = "mark-hybrid"
                    else:
                        mark_class = "mark-semantic"
                    sentence_html_content = f'<mark class="{mark_class}">{html.escape(sent_text)}</mark>'
            else:
                sentence_html_content = html.escape(sent_text)
                
            html_parts.append(sentence_html_content)
            last_offset = end
            
        # Append remaining trailing text
        if last_offset < len(text):
            raw_tail = text[last_offset:]
            html_parts.append(html.escape(raw_tail).replace("\n", "<br>"))
            
        return "".join(html_parts)

    @classmethod
    def generate_report(cls, data: dict) -> bytes:
        """
        Builds the HTML report structure and compiles it to PDF.
        """
        filename = data.get("filename", "unknown_document.txt")
        text = data.get("text", "")
        char_count = data.get("char_count", 0)
        sentence_count = data.get("sentence_count", 0)
        sentences = data.get("sentences", [])
        
        analysis = data.get("analysis", {}) or {}
        plag_score_float = analysis.get("plagiarism_score", 0.0)
        plag_score_pct = int(round(plag_score_float * 100))
        total_sents = analysis.get("total_sentences", 0)
        plag_sents_count = analysis.get("plagiarized_sentences_count", 0)
        lexical_count = analysis.get("lexical_matches_count", 0)
        semantic_count = analysis.get("semantic_matches_count", 0)
        hybrid_count = analysis.get("hybrid_matches_count", 0)
        matches = analysis.get("matches", [])
        
        # Calculate percentages
        lexical_pct = int(round((lexical_count / total_sents) * 100)) if total_sents > 0 else 0
        semantic_pct = int(round((semantic_count / total_sents) * 100)) if total_sents > 0 else 0
        hybrid_pct = int(round((hybrid_count / total_sents) * 100)) if total_sents > 0 else 0
        original_pct = max(0, 100 - lexical_pct - semantic_pct - hybrid_pct)
        
        # Format current timestamp
        current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Group top reference sources
        sources_summary = {}
        for m in matches:
            ref = m.get("matched_sentence", {})
            doc_id = ref.get("doc_id", "unknown")
            doc_title = ref.get("doc_title", "Unknown Reference")
            doc_author = ref.get("doc_author", "N/A")
            doc_source = ref.get("doc_source", "N/A")
            score = m.get("score", 0.0)
            m_type = m.get("match_type", "lexical")
            
            if doc_id not in sources_summary:
                sources_summary[doc_id] = {
                    "title": doc_title,
                    "author": doc_author,
                    "source": doc_source,
                    "count": 0,
                    "max_score": 0.0,
                    "types": set()
                }
            sources_summary[doc_id]["count"] += 1
            sources_summary[doc_id]["max_score"] = max(sources_summary[doc_id]["max_score"], score)
            sources_summary[doc_id]["types"].add(m_type)
            
        sorted_sources = sorted(
            sources_summary.values(),
            key=lambda x: x["count"],
            reverse=True
        )
        
        # Build the sources table HTML
        sources_table_rows = ""
        if sorted_sources:
            for idx, src in enumerate(sorted_sources, 1):
                types_str = " / ".join(list(src["types"])).upper()
                max_score_pct = int(round(src["max_score"] * 100))
                sources_table_rows += f"""
                <tr>
                    <td>{idx}</td>
                    <td>
                        <strong>{html.escape(src["title"])}</strong><br>
                        <span style="font-size: 8pt; color: #64748b;">{html.escape(src["author"])} — {html.escape(src["source"])}</span>
                    </td>
                    <td>{src["count"]}</td>
                    <td><span class="badge badge-{list(src["types"])[0]}">{types_str}</span></td>
                    <td><strong>{max_score_pct}%</strong></td>
                </tr>
                """
        else:
            sources_table_rows = """
            <tr>
                <td colspan="5" style="text-align: center; color: #64748b; padding: 20px;">
                    No plagiarism sources detected. Document is 100% original.
                </td>
            </tr>
            """
            
        # Reconstruct highlighted text
        highlighted_content = cls._get_highlighted_html(text, sentences, matches)
        
        # Build detailed matched segments comparison
        detailed_comparisons = ""
        if matches:
            for idx, m in enumerate(matches, 1):
                q_sent = m["query_sentence"]["text"]
                r_sent = m["matched_sentence"]["text"]
                ref_title = m["matched_sentence"]["doc_title"]
                ref_citation = f"{m['matched_sentence']['doc_author']} — {m['matched_sentence']['doc_source']}"
                score_pct = int(round(m["score"] * 100))
                m_type = m["match_type"]
                type_label = "Lexical Match" if m_type == "lexical" else ("Hybrid Match" if m_type == "hybrid" else "Semantic Match")
                badge_class = "badge-lexical" if m_type == "lexical" else ("badge-hybrid" if m_type == "hybrid" else "badge-semantic")
                
                detailed_comparisons += f"""
                <div class="match-item">
                    <div class="match-item-header">
                        <span class="match-item-title">Segment #{idx}</span>
                        <div>
                            <span class="badge {badge_class}">{type_label}</span>
                            <span class="badge {badge_class}">{score_pct}% Similarity</span>
                        </div>
                    </div>
                    <div class="match-item-body">
                        <div style="font-size: 8.5pt; color: #64748b; margin-bottom: 8px;">
                            <strong>Reference Source:</strong> {html.escape(ref_title)} ({html.escape(ref_citation)})
                        </div>
                        <div class="comparison-grid">
                            <div class="comparison-column">
                                <div class="comparison-label">Analyzed Text</div>
                                <blockquote class="comparison-text">{html.escape(q_sent)}</blockquote>
                            </div>
                            <div class="comparison-column">
                                <div class="comparison-label">Source Text</div>
                                <blockquote class="comparison-text">{html.escape(r_sent)}</blockquote>
                            </div>
                        </div>
                    </div>
                </div>
                """
        else:
            detailed_comparisons = """
            <div style="text-align: center; color: #64748b; border: 1px dashed #cbd5e1; border-radius: 8px; padding: 30px;">
                No matches to break down.
            </div>
            """
            
        # Overall HTML Template
        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Lemma Plagiarism Analysis Report</title>
    <style>
        @page {{
            size: A4;
            margin: 20mm 15mm 20mm 15mm;
            @bottom-right {{
                content: "Page " counter(page) " of " counter(pages);
                font-family: 'Outfit', 'Inter', 'Helvetica Neue', sans-serif;
                font-size: 8pt;
                color: #64748b;
            }}
            @bottom-left {{
                content: "Lemma Academic Integrity Platform";
                font-family: 'Outfit', 'Inter', 'Helvetica Neue', sans-serif;
                font-size: 8pt;
                color: #64748b;
            }}
        }}
        
        body {{
            font-family: 'Inter', 'Helvetica Neue', Arial, sans-serif;
            color: #1e293b;
            line-height: 1.5;
            margin: 0;
            padding: 0;
        }}

        h1, h2, h3, h4 {{
            font-family: 'Outfit', 'Helvetica Neue', Arial, sans-serif;
            color: #0f172a;
            margin-top: 0;
            font-weight: 700;
        }}

        .header {{
            border-bottom: 2px solid #e2e8f0;
            padding-bottom: 15px;
            margin-bottom: 20px;
        }}

        .header-title-container {{
            margin-bottom: 5px;
        }}

        .header-title {{
            font-size: 22pt;
            font-weight: 800;
            color: #0f172a;
            margin: 0;
            letter-spacing: -0.5px;
        }}

        .header-subtitle {{
            font-size: 8.5pt;
            color: #64748b;
            margin: 5px 0 0 0;
            text-transform: uppercase;
            letter-spacing: 1.5px;
            font-weight: 600;
        }}

        .meta-table {{
            width: 100%;
            margin-top: 15px;
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            border-collapse: separate;
            border-spacing: 15px 8px;
        }}

        .meta-label {{
            font-weight: 600;
            color: #475569;
            font-size: 9pt;
            width: 30%;
        }}

        .meta-value {{
            color: #0f172a;
            font-size: 9pt;
        }}

        .section {{
            margin-bottom: 30px;
        }}

        .section-title {{
            font-size: 13pt;
            border-bottom: 1px solid #e2e8f0;
            padding-bottom: 6px;
            margin-bottom: 12px;
            font-weight: 700;
            color: #0f172a;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        /* Metrics layout */
        .metrics-table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 10px 0;
            margin-bottom: 20px;
        }}

        .metric-card {{
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            padding: 12px;
            text-align: center;
            background-color: #ffffff;
            width: 20%;
        }}

        .metric-card.primary {{
            background-color: #0f172a;
            color: #ffffff;
            border-color: #0f172a;
        }}

        .metric-value {{
            font-size: 24pt;
            font-weight: 800;
            margin: 5px 0;
            font-family: 'Outfit', 'Helvetica Neue', Arial, sans-serif;
        }}

        .metric-label {{
            font-size: 8pt;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #64748b;
            font-weight: 600;
        }}

        .metric-card.primary .metric-label {{
            color: #94a3b8;
        }}

        /* Content highlighting */
        .content-box {{
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            padding: 15px;
            background-color: #ffffff;
            font-size: 10pt;
            text-align: justify;
        }}

        .mark-lexical {{
            background-color: #fee2e2;
            color: #991b1b;
            border-bottom: 1px solid #fca5a5;
        }}

        .mark-semantic {{
            background-color: #f3e8ff;
            color: #6b21a8;
            border-bottom: 1px solid #d8b4fe;
        }}

        .mark-hybrid {{
            background-color: #fef3c7;
            color: #92400e;
            border-bottom: 1px solid #fde68a;
        }}

        /* Tables */
        table.sources-table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }}

        table.sources-table th, table.sources-table td {{
            border: 1px solid #e2e8f0;
            padding: 8px 10px;
            text-align: left;
            font-size: 9pt;
        }}

        table.sources-table th {{
            background-color: #f1f5f9;
            font-weight: 700;
            color: #334155;
        }}

        table.sources-table tr:nth-child(even) {{
            background-color: #f8fafc;
        }}

        .badge {{
            display: inline-block;
            padding: 2px 6px;
            font-size: 7.5pt;
            font-weight: 600;
            border-radius: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .badge-lexical {{
            background-color: #fee2e2;
            color: #991b1b;
            border: 1px solid #fca5a5;
        }}

        .badge-semantic {{
            background-color: #f3e8ff;
            color: #6b21a8;
            border: 1px solid #d8b4fe;
        }}

        .badge-hybrid {{
            background-color: #fef3c7;
            color: #92400e;
            border: 1px solid #fde68a;
        }}

        /* Detailed Comparisons */
        .match-item {{
            border: 1px solid #e2e8f0;
            border-radius: 6px;
            margin-bottom: 12px;
            background-color: #ffffff;
            page-break-inside: avoid;
        }}

        .match-item-header {{
            background-color: #f8fafc;
            border-bottom: 1px solid #e2e8f0;
            padding: 8px 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}

        .match-item-title {{
            font-size: 9pt;
            font-weight: 700;
            color: #334155;
            margin: 0;
        }}

        .match-item-body {{
            padding: 12px;
        }}

        .comparison-grid {{
            display: table;
            width: 100%;
            table-layout: fixed;
            margin-top: 5px;
        }}

        .comparison-column {{
            display: table-cell;
            width: 50%;
            vertical-align: top;
            padding-right: 10px;
        }}

        .comparison-column:last-child {{
            padding-right: 0;
            padding-left: 10px;
        }}

        .comparison-label {{
            font-weight: 600;
            color: #64748b;
            margin-bottom: 4px;
            font-size: 8pt;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}

        .comparison-text {{
            background-color: #f8fafc;
            border: 1px solid #f1f5f9;
            border-radius: 4px;
            padding: 8px;
            margin: 0;
            font-style: italic;
            font-size: 8.5pt;
            color: #334155;
        }}
        
        .page-break {{
            page-break-before: always;
        }}
    </style>
</head>
<body>
    <div class="header">
        <div class="header-title-container">
            <h1 class="header-title">Academic Integrity & Plagiarism Analysis</h1>
            <div class="header-subtitle">Lemma Plagiarism Detection Engine</div>
        </div>
        <table class="meta-table">
            <tr>
                <td class="meta-label">Analyzed Document:</td>
                <td class="meta-value">{html.escape(filename)}</td>
                <td class="meta-label">Date Generated:</td>
                <td class="meta-value">{current_time}</td>
            </tr>
            <tr>
                <td class="meta-label">Total Characters:</td>
                <td class="meta-value">{char_count:,}</td>
                <td class="meta-label">Total Sentences:</td>
                <td class="meta-value">{sentence_count:,}</td>
            </tr>
        </table>
    </div>

    <div class="section">
        <div class="section-title">Integrity Summary</div>
        <table class="metrics-table">
            <tr>
                <td class="metric-card primary">
                    <div class="metric-label">Plagiarism Score</div>
                    <div class="metric-value">{plag_score_pct}%</div>
                </td>
                <td class="metric-card">
                    <div class="metric-label">Lexical Matches</div>
                    <div class="metric-value" style="color: #ef4444;">{lexical_pct}%</div>
                    <div style="font-size: 7.5pt; color: #64748b;">{lexical_count} sentence(s)</div>
                </td>
                <td class="metric-card">
                    <div class="metric-label">Hybrid Matches</div>
                    <div class="metric-value" style="color: #f59e0b;">{hybrid_pct}%</div>
                    <div style="font-size: 7.5pt; color: #64748b;">{hybrid_count} sentence(s)</div>
                </td>
                <td class="metric-card">
                    <div class="metric-label">Semantic Matches</div>
                    <div class="metric-value" style="color: #8b5cf6;">{semantic_pct}%</div>
                    <div style="font-size: 7.5pt; color: #64748b;">{semantic_count} sentence(s)</div>
                </td>
                <td class="metric-card">
                    <div class="metric-label">Original Content</div>
                    <div class="metric-value" style="color: #10b981;">{original_pct}%</div>
                    <div style="font-size: 7.5pt; color: #64748b;">{total_sents - plag_sents_count} sentence(s)</div>
                </td>
            </tr>
        </table>
    </div>

    <div class="section">
        <div class="section-title">Top Reference Sources Matched</div>
        <table class="sources-table">
            <thead>
                <tr>
                    <th style="width: 5%;">#</th>
                    <th style="width: 55%;">Source Document Details</th>
                    <th style="width: 12%;">Matches</th>
                    <th style="width: 16%;">Match Type</th>
                    <th style="width: 12%;">Max Sim</th>
                </tr>
            </thead>
            <tbody>
                {sources_table_rows}
            </tbody>
        </table>
    </div>

    <div class="page-break"></div>

    <div class="section">
        <div class="section-title">Document Content Analysis</div>
        <div class="content-box">
            {highlighted_content}
        </div>
    </div>

    <div class="page-break"></div>

    <div class="section">
        <div class="section-title">Segment-by-Segment Matching Breakdown</div>
        {detailed_comparisons}
    </div>
</body>
</html>
"""
        # Compile HTML string to PDF bytes via WeasyPrint
        # Compile HTML string to PDF bytes via WeasyPrint
        if not WEASYPRINT_AVAILABLE:
            raise Exception("WeasyPrint is not available on this system due to missing GTK dependencies.")
        pdf_bytes = io.BytesIO()
        HTML(string=html_content).write_pdf(target=pdf_bytes)
        return pdf_bytes.getvalue()
