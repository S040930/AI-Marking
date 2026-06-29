## AI Agent as Co-worker for Course Work Marking Automation (Digital Scanner)

SURF 2026 Project Presentation 

Project No.: SURF-2026-0031 

Dr Yi Chen 

June 2026 

## Table of Contents / CONTENTS

01 Project Overview & Research Background 

02 System Design & Technology Selection 

03 Implementation & Development 

04 Testing, Validation & Results 

05 Conclusion & Future Work 

## 01

## Project Overview

## 01. Project Overview

## Research Background

Automated marking systems are increasingly important in higher education 

Growing student numbers make manual marking time-consuming and inconsistent 

AI technologies offer opportunities for intelligent marking assistance 

Need for systems that maintain assessment quality whilst reducing workload 

## 01. Research Objectives

## Primary Objectives

Design AI agent for automated marking assistance 

Integrate digital scanning for coursework processing 

Validate system with real coursework samples 

Evaluate accuracy vs. manual marking 

## Innovation Points

First AI co-worker marking system at XJTLU 

Combines digital scanning with LLM marking 

Multi-format support (PDF, DOCX, images) 

Rubric-based adaptive scoring 

## 02

## System Design & Technology

## 02. System Design

## System Architecture

Frontend: Web interface for file upload and result display 

Backend: Python Flask/FastAPI for API and business logic 

AI Engine: LLM integration (GPT/Claude) for marking 

Database: Store coursework, rubrics, and marking results 

Digital Scanner: OCR and document parsing module 

## 02. Technology Selection

## Technology Stack

## Programming Language:

Python - primary development language 

JavaScript - frontend interaction 

## AI Model:

OpenAI GPT-4 API / Claude API 

Local LLM (optional, for privacy) 

## Framework:

Flask or FastAPI - backend framework 

React or Vue.js - frontend 

## Database:

SQLite (development) 

## 03

## Implementation & Development

## 03. Core Development

## Digital Scanner & AI Integration (Weeks 5-6)

Document Parsing: PDF, DOCX, image scanning with OCR 

AI Agent Integration: API connection to LLM for marking 

Rubric-based Scoring: Define criteria and scoring logic 

MVP Development: Minimal viable product for initial testing 

File Upload Interface: Drag-and-drop coursework upload 

## 03. AI Prompting Strategy

## Prompt Engineering for Marking

Rubric-based Prompts: Provide marking criteria in prompt 

Few-shot Learning: Include example marked coursework 

Structured Output: Request JSON format for easy parsing 

Chain-of-Thought: Ask AI to explain reasoning before scoring 

Self-verification: AI reviews its own marking for consistency 

## 04

## Testing, Validation & Results

## 04. Testing & Validation

## Validation Methodology (Weeks 7-8)

Test Dataset: Past coursework (anonymised) from XJTLU modules 

Comparison: AI marking vs. human marker grades 

Metrics: Accuracy, consistency, completeness of feedback 

Prompt Refinement: Iterate based on validation results 

User Feedback: Supervisor and peer review of system 

## 04. Expected Results

## Performance Metrics

Marking Accuracy: ≥ 85% agreement with human markers 

Time Efficiency: ≥ 60% reduction in marking time 

Feedback Quality: Actionable suggestions for improvement 

Scalability: Handle 50+ submissions simultaneously 

## 05

## Conclusion & Future Work

## Research Conclusion

Developed AI agent system for automated coursework marking assistance 

Integrated digital scanning capabilities for multi-format document processing 

Established validation methodology comparing AI vs. human marking 

Demonstrated feasibility of AI-assisted marking in higher education 

Provided open-source foundation for future research and development 

## Next Steps & Extensions

Multi-module Support: Extend to different assessment types 

Improved OCR: Better handwriting recognition for scanned work 

Cross-marking Validation: Multiple AI agents for consensus 

Feedback Personalisation: Tailored suggestions for each student 

Open-source Release: Publish on GitHub for community use 