# BUSINESS SCIENCE UNIVERSITY
# PYTHON FOR GENERATIVE AI COURSE
# FIRST AI-POWERED BUSINESS APP: PART 2
# ***
# GOAL: Exposure to using LLM's, Document Loaders, and Prompts

# streamlit run 
# 02-First-AI-Business-Summarization-App/02_document_summarizer_app.py

from langchain_community.document_loaders import PyPDFLoader
from langchain_openai import ChatOpenAI
from langchain.chains.llm import LLMChain
from langchain_core.prompts import PromptTemplate
from langchain.chains.combine_documents.stuff import StuffDocumentsChain
from langchain.chains.summarize import load_summarize_chain

import yaml
import streamlit as st
import os
from tempfile import NamedTemporaryFile

# Load API Key
OPENAI_API_KEY = os.getenv('key')

# 1.0 LOAD AND SUMMARIZE FUNCTION

def load_and_summarize(file, use_template =False):
    
    with NamedTemporaryFile(delete=False, suffix= ".pdf") as tmp: 
        tmp.write(file.getvalue())
        file_path = tmp.name
    try: 
        
        loader = PyPDFLoader(file_path)
        docs = loader.load()
        
        model = ChatOpenAI(
            model= "gpt-4-turbo",
            temperature= 0, 
            api_key= OPENAI_API_KEY
            )  
    
        if use_template:
            #Bullets
            
            prompt_template = """
            Write a congressional bill analysis from the following bill:
            {text}

            Use the following Markdown format:
            # Bill Analysis
            
            #History
            Additionally, include any historical context, previous attempts to address similar issues, or comparisons to similar legislation passed in the past.

            #Who?
            Who are the key stakeholders affected by the bill? Include groups that will benefit or face challenges due to the bill.
            
            #What?
            What is the main objective of this bill? Provide a summary of the key points and sections.
            
            #When?
            When is the bill expected to take effect, and over what timeline? Detail any deadlines, phased rollouts, or sunset provisions.

            #Where?
            Where will the impact of this bill be most significant? Highlight geographic areas or industries that will see the most change.
            
            #Why?
            Why was this bill introduced? Explain the background, including societal, economic, or political motivations behind its introduction.
            
            #How?
            How is the bill intended to be implemented? Describe the processes, agencies, or mechanisms that will ensure the bill takes effect and the expected outcomes.
            
            #Challenges
            What are the potential challenges or criticisms of the bill? Provide insight into any controversies or opposition to the bill.
            """
            prompt = PromptTemplate(input_variables= ["text"], template=prompt_template)

            llm_chain = LLMChain(prompt = prompt, llm = model)

            stuff_chain = StuffDocumentsChain(llm_chain = llm_chain, 
                    document_variable_name= "text")

            response = stuff_chain.invoke(docs)
        
        else:
            #No bullets
            summarizer_chain = load_summarize_chain(llm = model, 
                                         chain_type= "stuff")
            response = summarizer_chain.invoke(docs)
            
    finally: 
        
        os.remove(file_path)
    return response['output_text'] 

# 2.0 STREAMLIT INTERFACE
st.title("Bill Analyzer")

st.subheader("Upload a PDF Document:")
uploaded_file = st.file_uploader("Choose a file",
                         type = "pdf")
if uploaded_file is not None:
    use_template = st.checkbox(" Use numbered bullet points? (if not a paragraph will be returned)")

    if st.button("Analyze Document"):
        with st.spinner("Analyzing..."):
             
             summary = load_and_summarize(uploaded_file, use_template)
             
             st.markdown(summary)
else:
    
    st.write("No file uploaded. Please upload a PDF file to proceed")
# CONCLUSIONS:
#  1. WE CAN SEE HOW APPLICATIONS LIKE STRE
# AMLIT ARE A NATURAL INTERFACE TO AUTOMATING THE LLM TASKS
#  2. BUT WE CAN DO MORE. 
#     - WHAT IF WE HAD A FULL DIRECTORY OF PDF'S?
#     - WHAT IF WE WANTED TO DO MORE COMPLEX ANALYSIS?

