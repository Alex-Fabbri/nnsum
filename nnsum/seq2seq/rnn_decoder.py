import torch
import torch.nn as nn

from .rnn_state import RNNState
from .search_state import SearchState
from .no_attention import NoAttention
from .dot_attention import DotAttention


class RNNDecoder(nn.Module):
    def __init__(self, embedding_context, 
                 hidden_dim=512, num_layers=1,
                 rnn_cell="GRU", attention="none",
                 copy_attention="none"):
        super(RNNDecoder, self).__init__()

        rnn_cell = rnn_cell.upper()
        assert rnn_cell in ["LSTM", "GRU", "RNN"]
        assert hidden_dim > 0
        assert num_layers > 0
        assert attention in ["none", "dot"]

        self._emb_ctx = embedding_context        
        self._rnn = getattr(nn, rnn_cell)(
            embedding_context.output_size, hidden_dim, num_layers=num_layers)

        pred_dim = hidden_dim if attention == "none" else 2 * hidden_dim
        self._predictor = nn.Linear(pred_dim, len(self._emb_ctx.vocab))
      
        if attention == "none":
            self._attention = NoAttention()
        else:
            self._attention = DotAttention()
       
#        if copy_attention == "reuse":
#            self._copy_attention_mode = "reuse"
#        elif copy_attention == "dot":
#            self._copy_attention_mode = "dot"
#            self._pre_copy_attention = nn.Sequential(
#                    nn.Linear(pred_dim, hidden_dim), nn.Tanh())
#            self._copy_attention = DotAttention()
#            self._pointer_switch = nn.Sequential(
#                nn.Linear(pred_dim, 1), nn.Sigmoid())
#        else:
#        self._copy_attention_mode = "none"

    def next_state(self, prev_rnn_state, inputs=None, context=None, 
                   context_mask=None, compute_log_probability=False,
                   context_vocab_map=None):

        rnn_input = self.embedding_context(inputs)

        rnn_output, rnn_state = self.rnn(rnn_input, prev_rnn_state)
        
        predictor_input, context_attention = self.attention(
            context, rnn_output, mask=context_mask)
        
        logits = self._predictor(predictor_input)

        next_state = SearchState(
            logits=logits, rnn_state=RNNState.new_state(rnn_state),
            rnn_outputs=rnn_output)

        if context_attention is not None:
            next_state["context_attention"] = context_attention

#        if self.copy_attention_mode == "dot":
#            print(target_context_features["tokens"])
#            copy_prob = self._pointer_switch(predictor_input).squeeze(-1)
#            query = self._pre_copy_attention(predictor_input)
#             # TODO avoid doing the composition op.
#            _, copy_attention = self._copy_attention(
#               context, query, mask=context_mask)
#            gen_prob = (1 - copy_prob)
#            gen_vocab_prob = torch.softmax(logits, dim=2)
#            print()
#            print(copy_attention)
#            print(copy_prob)
#            print(gen_prob)
#            print(gen_vocab_prob)
#            input()

        if compute_log_probability:
            next_state["log_probability"] = torch.log_softmax(logits, dim=2)

        return next_state

    @property
    def rnn(self):
        return self._rnn

    @property
    def embedding_context(self):
        return self._emb_ctx

    @property
    def attention(self):
        return self._attention

    @property
    def predictor(self):
        return self._predictor

    @property
    def copy_attention_mode(self):
        return self._copy_attention_mode



















    def log_likelihood(self, inputs, outputs, context, encoder_state,
                       context_mask=None):
        rnn_input = self.embedding_context(inputs)
        rnn_output, rnn_state = self.rnn(rnn_input, encoder_state)
        predictor_input, attention = self.attention(context, rnn_output,
                                                    mask=context_mask)
        logits = self.predictor(predictor_input)

    def forward(self, inputs, context, state, context_mask=None):
        decoder_output, state = self._rnn(self._emb_ctx(inputs), state)
        predictor_input, attn = self._attention(context, decoder_output,
                                                mask=context_mask)
        attn_dict = {"attention": attn}
        if self.copy_attention_mode == "reuse":
            attn_dict["copy_attention"] = attn

        logits = self._predictor(predictor_input)
        return logits, attn_dict, state

    def decode(self, context, state, max_steps=1000, return_log_probs=False,
               return_attention=False):
        batch_size = context.size(0)
        
        start_idx = self.embedding_context.vocab.start_index
        stop_idx = self.embedding_context.vocab.stop_index
        pad_idx = self.embedding_context.vocab.pad_index

        inputs = context.data.new(
            batch_size).long().fill_(start_idx).view(-1, 1)

        predicted_tokens = []
        token_log_probs = []
        # TODO Make general empty state
        attention_steps = []    
    
        active_items = inputs.ne(stop_idx).view(-1)
        for step in range(max_steps):
            logits, attn, state = self.forward(inputs, context, state)
            attention_steps.append(attn)
            a, next_tokens = logits.max(2)
            
            if return_log_probs:
                lp_step = logits.gather(2, next_tokens.view(1, -1, 1)) \
                    - torch.logsumexp(logits, dim=2, keepdim=True)
                lp_step.data.view(-1).masked_fill_(~active_items, 0)
                token_log_probs.append(lp_step)  
            
            inputs = next_tokens.t()
            inputs.view(-1).data.masked_fill_(~active_items, pad_idx)
            predicted_tokens.append(inputs)
            active_items = active_items * inputs.view(-1).ne(stop_idx)
            if torch.all(~active_items):
                break
        predicted_tokens = torch.cat(predicted_tokens, dim=1)

        if return_log_probs:
            
            return predicted_tokens, attention_steps, torch.cat(
                token_log_probs, 0)
        else:
            return predicted_tokens, attention_steps

    def start_inputs(self, batch_size):
        return torch.LongTensor(
            [[self.embedding_context.vocab.start_index]] * batch_size)
        inputs = {n: torch.LongTensor([[v.start_index]] * batch_size)   
                  for n, v in self.embedding_context.named_vocabs.items()}
        return inputs 

    def initialize_parameters(self):
        print(" Initializing decoder embedding context parameters.")
        self.embedding_context.initialize_parameters()
        print(" Initializing decoder parameters.")
        for name, param in self.rnn.named_parameters():
            if "weight" in name:
                nn.init.xavier_normal_(param)
            else:
                nn.init.constant_(param, 1.)
