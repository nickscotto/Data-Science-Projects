# BUSINESS SCIENCE UNIVERSITY
# PYTHON FOR GENERATIVE AI COURSE
# FIRST AI-POWERED BUSINESS APP: PART 2
# ***
# GOAL: Exposure to using LLM's, Document Loaders, and Prompts

# streamlit run 03-First-AI-Business-App/02_document_summarizer_app.py


import yaml

from langchain_community.document_loaders import PyPDFLoader
from langchain_openai import ChatOpenAI
from langchain.chains.llm import LLMChain
from langchain_core.prompts import PromptTemplate
from langchain.chains.combine_documents.stuff import StuffDocumentsChain

import streamlit as st
import os
from tempfile import NamedTemporaryFile

# Load API Key
OPENAI_API_KEY = yaml.safe_load(open('../credentials.yml'))['openai']

# 1.0 LOAD AND SUMMARIZE FUNCTION



# 2.0 STREAMLIT INTERFACE



# CONCLUSIONS:
#  1. WE CAN SEE HOW APPLICATIONS LIKE STREAMLIT ARE A NATURAL INTERFACE TO AUTOMATING THE LLM TASKS
#  2. BUT WE CAN DO MORE. 
#     - WHAT IF WE HAD A FULL DIRECTORY OF PDF'S?
#     - WHAT IF WE WANTED TO DO MORE COMPLEX ANALYSIS?
